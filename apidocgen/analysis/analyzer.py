"""Orchestrates LLM analysis with the content-addressed cache."""
from __future__ import annotations

import fnmatch
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Config
from ..detectors.model import EndpointSpec
from ..docmodel.builder import DocBuilder
from ..docmodel.model import EndpointDoc
from ..llm.base import LLMClient, LLMError, extract_json
from ..project import Project
from ..util.tokens import estimate_tokens
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .units import PROMPT_VERSION, EndpointUnit, IntroUnit, TypeUnit, UnitBuilder, cache_key, content_hash

Progress = Callable[[str], None]


@dataclass
class UnitStatus:
    unit: Any                 # TypeUnit | EndpointUnit
    key: str
    cached: bool


@dataclass
class AnalysisPlan:
    type_units: List[UnitStatus] = field(default_factory=list)
    endpoint_units: List[UnitStatus] = field(default_factory=list)
    intro_unit: Optional[UnitStatus] = None
    docs: Dict[str, EndpointDoc] = field(default_factory=dict)
    specs: List[EndpointSpec] = field(default_factory=list)

    @property
    def all_units(self) -> List[UnitStatus]:
        return self.type_units + self.endpoint_units + ([self.intro_unit] if self.intro_unit else [])

    @property
    def misses(self) -> List[UnitStatus]:
        return [u for u in self.all_units if not u.cached]

    @property
    def hits(self) -> List[UnitStatus]:
        return [u for u in self.all_units if u.cached]

    def summary(self) -> Dict[str, Any]:
        miss_tokens = sum(u.unit.tokens for u in self.misses)
        hit_tokens = sum(u.unit.tokens for u in self.hits)
        return {
            "endpoints": len(self.specs),
            "type_units": len(self.type_units),
            "endpoint_units": len(self.endpoint_units),
            "cache_hits": len(self.hits),
            "cache_misses": len(self.misses),
            "estimated_input_tokens_to_send": miss_tokens + (len(self.misses) and estimate_tokens(SYSTEM_PROMPT)),
            "estimated_tokens_saved_by_cache": hit_tokens,
        }


@dataclass
class AnalysisReport:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    analysed_units: int = 0
    failed_units: List[str] = field(default_factory=list)
    stopped_reason: str = ""
    duration_s: float = 0.0


class Analyzer:
    def __init__(self, project: Project, client: Optional[LLMClient] = None) -> None:
        self.project = project
        self.cfg: Config = project.cfg
        self.client = client
        self.store = project.store
        self.units = UnitBuilder(self.cfg, project.index, self.store)
        self.doc_builder = DocBuilder(self.cfg, project.index, self.store)
        # Context changes affect descriptions even if Java source is unchanged. Keep mock
        # placeholders separate from real analyses without forcing model-specific caching.
        self.context = json.dumps({
            "application": self.cfg.get("project", "name") or self.cfg.get("doc", "system_name") or "",
            "business_context": self.cfg.get("project", "description") or "",
            "style_notes": self.cfg.get("llm", "style_notes") or "",
        }, ensure_ascii=False, sort_keys=True)
        provider = client.name if client is not None else self.cfg.get("llm", "provider")
        self.prompt_version = PROMPT_VERSION + ":" + content_hash(self.context + ("|mock" if provider == "mock" else "|real"))
        self.model_for_key: Optional[str] = None
        if self.cfg.get("analysis", "cache_key_includes_model", default=False):
            # derived from the configuration (not the client) so render/UI/prune compute the same keys as analyze
            self.model_for_key = (client.model if client is not None else None) or self.cfg.get("llm", "model")

    # ------------------------------------------------------------------ selection
    def select_specs(self, only: Optional[List[str]] = None) -> List[EndpointSpec]:
        include = self.cfg.get("doc", "include", default=[]) or []
        exclude = self.cfg.get("doc", "exclude", default=[]) or []
        selected: List[EndpointSpec] = []
        for spec in self.project.endpoints():
            if spec.http_method == "?":
                continue
            if include and not any(fnmatch.fnmatch(spec.id, pattern) for pattern in include):
                continue
            if exclude and any(fnmatch.fnmatch(spec.id, pattern) for pattern in exclude):
                continue
            if only and not any(fnmatch.fnmatch(spec.id, pattern) or pattern in spec.id for pattern in only):
                continue
            selected.append(spec)
        return self._sort_specs(selected)

    def _sort_specs(self, specs: List[EndpointSpec]) -> List[EndpointSpec]:
        order = self.cfg.get("doc", "endpoint_order", default="path")
        if order == "source":
            specs.sort(key=lambda spec: (spec.file_path, spec.handler_qname))
        elif order == "config":
            wanted = list(self.cfg.get("doc", "order", default=[]) or [])
            position = {endpoint_id: index for index, endpoint_id in enumerate(wanted)}
            specs.sort(key=lambda spec: (position.get(spec.id, len(position)), spec.path.lower(), spec.http_method))
        else:
            specs.sort(key=lambda spec: (spec.path.lower(), spec.http_method))
        return specs

    # ------------------------------------------------------------------ planning
    def plan(self, only: Optional[List[str]] = None) -> AnalysisPlan:
        plan = AnalysisPlan()
        plan.specs = self.select_specs(only)
        type_seen: Dict[str, TypeUnit] = {}
        for number, spec in enumerate(plan.specs, 1):
            doc = self.doc_builder.build(spec, number)
            plan.docs[spec.id] = doc
            for type_qname in doc.type_closure:
                if type_qname not in self.project.index.types:
                    continue
                if type_qname not in type_seen:
                    type_seen[type_qname] = self.units.type_unit(type_qname)
                type_seen[type_qname].used_by.append(spec.id)
            endpoint_unit = self.units.endpoint_unit(spec, doc)
            key = cache_key("endpoint", endpoint_unit.hash, self.prompt_version, self.model_for_key)
            plan.endpoint_units.append(UnitStatus(
                unit=endpoint_unit,
                key=key,
                cached=self.store.cache_get(key) is not None,
            ))
        for type_unit in type_seen.values():
            key = cache_key("type", type_unit.hash, self.prompt_version, self.model_for_key)
            plan.type_units.append(UnitStatus(
                unit=type_unit,
                key=key,
                cached=self.store.cache_get(key) is not None,
            ))
        if self.cfg.get("doc", "intro_llm", default=False) and not self.cfg.get("doc", "intro_file") and plan.specs:
            intro_unit = self.units.intro_unit(plan.specs, plan.docs)
            key = cache_key("intro", intro_unit.hash, self.prompt_version, self.model_for_key)
            plan.intro_unit = UnitStatus(
                unit=intro_unit,
                key=key,
                cached=self.store.cache_get(key) is not None,
            )
        hits = [unit.key for unit in plan.all_units if unit.cached]
        if hits:
            try:
                self.store.cache_touch(hits)
            except Exception:
                # statistics only - never fail a plan because another process holds the db
                pass
        return plan

    def intro_html(self, plan: AnalysisPlan) -> Optional[str]:
        """Cached LLM-written introduction (or None)."""
        if plan.intro_unit is None:
            return None
        r = self.store.cache_get(plan.intro_unit.key)
        if r is None and self.cfg.get("analysis", "stale_fallback", default=True):
            r = self.store.cache_latest_for_unit("intro", plan.intro_unit.unit.unit_id)
        return (r or {}).get("intro_html") or None

    # ------------------------------------------------------------------ running
    def run(self, plan: AnalysisPlan, progress: Optional[Progress] = None, dry_run: bool = False) -> AnalysisReport:
        report = AnalysisReport()
        t0 = time.time()
        log = progress or (lambda msg: None)
        if dry_run or self.client is None:
            report.stopped_reason = "dry-run" if dry_run else "no llm client"
            report.duration_s = time.time() - t0
            return report
        max_calls = self.cfg.get("llm", "max_calls_per_run")
        max_in = self.cfg.get("llm", "max_input_tokens_per_run")
        style = "Application context (use only rules supported by the source):\n" + self.context
        batch_tokens = int(self.cfg.get("analysis", "type_batch_tokens", default=9000))

        def budget_ok() -> bool:
            if max_calls is not None and report.calls >= int(max_calls):
                report.stopped_reason = f"max_calls_per_run={max_calls} reached"
                return False
            if max_in is not None and report.input_tokens >= int(max_in):
                report.stopped_reason = f"max_input_tokens_per_run={max_in} reached"
                return False
            return True

        # ---- type units in batches
        misses = [u for u in plan.type_units if not u.cached]
        batches = self._batch_by_tokens(misses, batch_tokens)
        for bi, batch in enumerate(batches, 1):
            if not budget_ok():
                break
            log(f"[types] batch {bi}/{len(batches)}: {', '.join(u.unit.name for u in batch)}")
            self._call_batch(batch, style, report, log, budget_ok)
        # ---- endpoint units one by one (they are large)
        ep_misses = [u for u in plan.endpoint_units if not u.cached]
        for i, u in enumerate(ep_misses, 1):
            if not budget_ok():
                break
            log(f"[endpoint] {i}/{len(ep_misses)}: {u.unit.unit_id} (~{u.unit.tokens} tokens)")
            self._call_batch([u], style, report, log, budget_ok)
        if plan.intro_unit is not None and not plan.intro_unit.cached and budget_ok():
            log("[intro] writing the introduction")
            self._call_batch([plan.intro_unit], style, report, log, budget_ok)
        report.duration_s = time.time() - t0
        return report

    @staticmethod
    def _batch_by_tokens(units: List[UnitStatus], batch_tokens: int) -> List[List[UnitStatus]]:
        """Greedily group units into batches that stay under ``batch_tokens`` each."""
        batches: List[List[UnitStatus]] = []
        cur: List[UnitStatus] = []
        cur_tokens = 0
        for u in units:
            if cur and cur_tokens + u.unit.tokens > batch_tokens:
                batches.append(cur)
                cur, cur_tokens = [], 0
            cur.append(u)
            cur_tokens += u.unit.tokens
        if cur:
            batches.append(cur)
        return batches

    def _unit_spec(self, unit: Any) -> Dict[str, Any]:
        if isinstance(unit, TypeUnit):
            return {
                "id": unit.unit_id,
                "kind": "type",
                "name": unit.name,
                "fields": unit.field_names,
                "enum_values": unit.enum_names,
            }
        if isinstance(unit, IntroUnit):
            return {"id": unit.unit_id, "kind": "intro", "name": unit.name}
        return {
            "id": unit.unit_id,
            "kind": "endpoint",
            "name": unit.name,
            "params": unit.param_names,
            "response_params": unit.response_names,
            "types": unit.type_names,
        }

    def _record(self, report: AnalysisReport, batch: List[UnitStatus], resp, dur_ms: int, status: str,
                error: Optional[str]) -> None:
        """Account one HTTP attempt in the report and the call log (every attempt is paid for)."""
        in_tok = resp.input_tokens if resp else 0
        out_tok = resp.output_tokens if resp else 0
        report.calls += 1
        report.input_tokens += in_tok
        report.output_tokens += out_tok
        report.cache_read_tokens += resp.cache_read_tokens if resp else 0
        report.cache_write_tokens += resp.cache_write_tokens if resp else 0
        assert self.client is not None
        self.store.log_call(self.client.name, self.client.model, batch[0].unit.kind, [u.unit.unit_id for u in batch],
                            in_tok, out_tok, resp.cache_read_tokens if resp else 0, resp.cache_write_tokens if resp else 0,
                            dur_ms, status, error)

    @staticmethod
    def _parse_results(text: str, batch: List[UnitStatus]) -> Dict[str, Any]:
        """Extract the per-unit result objects; raises ValueError for any unexpected shape."""
        data = extract_json(text)
        if not isinstance(data, dict):
            raise ValueError("model returned a JSON value that is not an object")
        results = data.get("results") if isinstance(data.get("results"), dict) else data
        if not isinstance(results, dict):
            raise ValueError("'results' is not an object")
        if len(batch) == 1 and batch[0].unit.unit_id not in results and ("fields" in results or "title" in results
                                                                          or "intro_html" in results):
            results = {batch[0].unit.unit_id: results}
        return results

    def _call_batch(self, batch: List[UnitStatus], style: str, report: AnalysisReport, log: Progress,
                    budget_ok: Optional[Callable[[], bool]] = None) -> None:
        assert self.client is not None
        budget_ok = budget_ok or (lambda: True)
        results, resp, error, aborted = self._request_results(batch, style, report, log, budget_ok)
        if aborted:
            return
        if not results:
            self._handle_empty_results(batch, style, report, log, budget_ok, error)
            return
        self._store_results(batch, results, resp, report, log)

    def _request_results(self, batch: List[UnitStatus], style: str, report: AnalysisReport, log: Progress,
                         budget_ok: Callable[[], bool]
                         ) -> Tuple[Dict[str, Any], Any, Optional[str], bool]:
        """Ask the model for this batch, retrying once on a non-truncated bad answer.

        Returns ``(results, response, error, aborted)``; ``aborted`` marks a transport
        failure that has already been reported, so the caller should simply stop.
        """
        specs = [self._unit_spec(u.unit) for u in batch]
        user = build_user_prompt(specs, [u.unit.payload for u in batch], style)
        results: Dict[str, Any] = {}
        resp = None
        error: Optional[str] = None
        truncated = False
        for attempt in range(2):
            if attempt > 0 and (truncated or not budget_ok()):
                break  # a truncated answer is deterministic: retrying the same request is wasted
            started = time.time()
            try:
                resp = self.client.complete(SYSTEM_PROMPT, user, json_mode=True)
            except LLMError as e:
                self._record(report, batch, None, int((time.time() - started) * 1000), "error", str(e))
                log(f"   ! {e}")
                report.failed_units.extend(u.unit.unit_id for u in batch)
                return {}, None, str(e), True
            duration_ms = int((time.time() - started) * 1000)
            truncated = bool(getattr(resp, "truncated", False))
            try:
                results = self._parse_results(resp.text, batch)
                status, error = "ok", None
            except (ValueError, json.JSONDecodeError) as e:
                results = {}
                status = "truncated" if truncated else "bad-json"
                error = ("response truncated (raise llm.max_output_tokens or lower analysis.type_batch_tokens)"
                         if truncated else f"invalid JSON from model: {e}")
            self._record(report, batch, resp, duration_ms, status, error)
            if results:
                break
        return results, resp, error, False

    def _handle_empty_results(self, batch: List[UnitStatus], style: str, report: AnalysisReport, log: Progress,
                              budget_ok: Callable[[], bool], error: Optional[str]) -> None:
        """Halve the batch and retry, or give up and mark every unit in it as failed."""
        if len(batch) > 1 and budget_ok():
            half = len(batch) // 2
            log(f"   ! {error}; splitting batch of {len(batch)} into {half} + {len(batch) - half}")
            self._call_batch(batch[:half], style, report, log, budget_ok)
            self._call_batch(batch[half:], style, report, log, budget_ok)
        else:
            log(f"   ! {error or 'no result'}")
            report.failed_units.extend(u.unit.unit_id for u in batch)

    def _store_results(self, batch: List[UnitStatus], results: Dict[str, Any], resp: Any,
                       report: AnalysisReport, log: Progress) -> None:
        """Sanitize each unit's result and cache it, charging tokens by payload share."""
        in_tok = resp.input_tokens if resp else 0
        out_tok = resp.output_tokens if resp else 0
        total_payload = sum(max(1, u.unit.tokens) for u in batch)
        for u in batch:
            result = results.get(u.unit.unit_id)
            if not isinstance(result, dict):
                report.failed_units.append(u.unit.unit_id)
                log(f"   ! no result for {u.unit.unit_id}")
                continue
            try:
                result = self._sanitize(u.unit, result)
            except (AttributeError, TypeError, ValueError) as e:
                report.failed_units.append(u.unit.unit_id)
                log(f"   ! unusable result for {u.unit.unit_id}: {e}")
                continue
            share = max(1, u.unit.tokens) / total_payload
            self.store.cache_put(u.key, u.unit.kind, u.unit.unit_id, u.unit.hash, self.client.name, self.client.model,
                                 self.prompt_version, result, int(in_tok * share), int(out_tok * share))
            u.cached = True
            report.analysed_units += 1
        cache_read = resp.cache_read_tokens if resp else 0
        log(f"   ok: {in_tok} in / {out_tok} out tokens" + (f" (prompt cache read {cache_read})" if cache_read else ""))

    @staticmethod
    def _sanitize(unit: Any, r: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(unit, IntroUnit):
            return {"intro_html": str(r.get("intro_html", "") or "")}
        if isinstance(unit, TypeUnit):
            return Analyzer._sanitize_type(r)
        return Analyzer._sanitize_endpoint(unit, r)

    @staticmethod
    def _sanitize_type(raw: Dict[str, Any]) -> Dict[str, Any]:
        fields = raw.get("fields") or {}
        if not isinstance(fields, dict):
            raise ValueError("'fields' must be an object")
        clean: Dict[str, Any] = {}
        for name, value in fields.items():
            if isinstance(value, str):
                value = {"description": value}
            if isinstance(value, dict):
                clean[name] = {
                    "description": str(value.get("description", "") or ""),
                    "example": value.get("example"),
                }
        enum_values = raw.get("enum_values") or {}
        if not isinstance(enum_values, dict):
            enum_values = {}
        return {
            "description": str(raw.get("description", "") or ""),
            "fields": clean,
            "enum_values": {str(key): str(value) for key, value in enum_values.items()},
        }

    @staticmethod
    def _param_descriptions(raw: Any, allowed_names: List[str]) -> Dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        descriptions: Dict[str, str] = {}
        for name, value in raw.items():
            if name not in allowed_names:
                continue
            if isinstance(value, dict):
                descriptions[str(name)] = str(value.get("description", "") or "")
            else:
                descriptions[str(name)] = str(value)
        return descriptions

    @staticmethod
    def _sanitize_endpoint(unit: Any, raw: Dict[str, Any]) -> Dict[str, Any]:
        notes = raw.get("notes")
        if isinstance(notes, list):
            note_list = notes
        elif isinstance(notes, str):
            note_list = [notes]
        else:
            note_list = []
        error_codes = raw.get("error_codes") if isinstance(raw.get("error_codes"), list) else []
        extra = raw.get("response_fields_extra") if isinstance(raw.get("response_fields_extra"), list) else []
        return {
            "title": str(raw.get("title", "") or ""),
            "summary": str(raw.get("summary", "") or ""),
            "description": str(raw.get("description", "") or ""),
            "params": Analyzer._param_descriptions(raw.get("params"), unit.param_names),
            "notes": [str(note) for note in note_list if note],
            "error_codes": [
                {
                    "code": str(error.get("code", "")),
                    "title": str(error.get("title", "") or ""),
                    "note": str(error.get("note", "") or ""),
                }
                for error in error_codes
                if isinstance(error, dict) and error.get("code")
            ],
            "response_params": Analyzer._param_descriptions(raw.get("response_params"), unit.response_names),
            "response_fields_extra": [
                item for item in extra if isinstance(item, dict) and item.get("name")
            ],
            "business_rules": [str(rule) for rule in (raw.get("business_rules") or []) if rule],
        }

    # ------------------------------------------------------------------ results for rendering
    def results_for(self, spec: EndpointSpec, doc: EndpointDoc) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any], str]:
        """Return (type analyses by qname, endpoint analysis, status) using cache (+stale fallback)."""
        stale_ok = bool(self.cfg.get("analysis", "stale_fallback", default=True))
        type_analyses: Dict[str, Dict[str, Any]] = {}
        status = "cached"
        any_missing = False
        for type_qname in doc.type_closure:
            if type_qname not in self.project.index.types:
                continue
            type_unit = self.units.type_unit(type_qname)
            key = cache_key("type", type_unit.hash, self.prompt_version, self.model_for_key)
            cached = self.store.cache_get(key)
            if cached is None and stale_ok:
                cached = self.store.cache_latest_for_unit("type", type_qname)
                if cached is not None:
                    status = "stale"
            if cached is None:
                any_missing = True
            else:
                type_analyses[type_qname] = cached
        endpoint_unit = self.units.endpoint_unit(spec, doc)
        key = cache_key("endpoint", endpoint_unit.hash, self.prompt_version, self.model_for_key)
        endpoint_analysis = self.store.cache_get(key)
        if endpoint_analysis is None and stale_ok:
            endpoint_analysis = self.store.cache_latest_for_unit("endpoint", spec.id)
            if endpoint_analysis is not None:
                status = "stale"
        if endpoint_analysis is None:
            any_missing = True
        if any_missing and not type_analyses and endpoint_analysis is None:
            status = "none"
        elif any_missing:
            status = "partial" if status == "cached" else status
        return type_analyses, endpoint_analysis or {}, status

    def build_doc(self, spec: EndpointSpec, number: int) -> EndpointDoc:
        base = self.doc_builder.build(spec, number)
        type_res, ep, status = self.results_for(spec, base)
        return self.doc_builder.build(spec, number, type_res, ep, status)

    def prune_cache(self, plan: AnalysisPlan) -> int:
        """Drop cache rows that belong to no current unit hash (old versions of changed code)."""
        keep = [u.key for u in plan.type_units + plan.endpoint_units]
        return self.store.cache_prune(keep)
