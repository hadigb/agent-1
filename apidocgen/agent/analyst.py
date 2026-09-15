"""Senior analyst agent: scan the code graph and produce use-case specifications."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..analysis.analyzer import Analyzer
from ..detectors.model import EndpointSpec
from ..docmodel.builder import DocBuilder
from ..llm.base import LLMClient, LLMError, extract_json
from ..project import Project
from ..services.scan_service import build_controller_graph, scan_project
from .models import UseCaseSpec
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .renderer import render_ucs_html

Log = Callable[[str], None]


@dataclass
class AnalystRequest:
    mode: str = "new"  # new | existing
    endpoint_id: Optional[str] = None
    previous_analysis: str = ""
    new_requirement: str = ""
    dry_run: bool = False


@dataclass
class AnalystResult:
    specs: List[UseCaseSpec] = field(default_factory=list)
    html: str = ""
    document_id: Optional[int] = None
    calls: int = 0
    failed: List[str] = field(default_factory=list)
    seconds: float = 0.0
    scan: Dict[str, Any] = field(default_factory=dict)


class SeniorAnalyst:
    def __init__(self, project: Project, client: Optional[LLMClient]) -> None:
        self.project = project
        self.client = client
        self.analyzer = Analyzer(project, client)

    def select_specs(self, req: AnalystRequest) -> List[EndpointSpec]:
        specs = self.analyzer.select_specs()
        if req.mode == "existing":
            wanted = (req.endpoint_id or "").strip()
            if not wanted:
                write("existing service mode needs a path")
                return []
            specs = [s for s in specs if s.id == wanted or s.path == wanted or wanted in s.id]
        return specs

    def run(self, req: AnalystRequest, log: Optional[Log] = None) -> AnalystResult:
        write = log or (lambda _: None)
        t0 = time.time()
        result = AnalystResult()
        result.scan = scan_project(self.project, write)
        self.analyzer = Analyzer(self.project, self.client)
        specs = self.select_specs(req)
        if not specs:
            write("no endpoints selected")
            result.seconds = time.time() - t0
            result.html = render_ucs_html(self.project.cfg, [], req)
            return result
        write(f"analyst: {len(specs)} use case(s) from controller graph")
        graph = {g["id"]: g for g in build_controller_graph(self.project)}
        builder = DocBuilder(self.project.cfg, self.project.index, self.project.store)
        if req.dry_run or self.client is None:
            result.specs = [self._fallback_spec(s, graph.get(s.id, {}), req) for s in specs]
            result.html = render_ucs_html(self.project.cfg, result.specs, req)
            result.seconds = time.time() - t0
            return result
        for spec in specs:
            write(f"  analysing {spec.id}")
            try:
                uc = self._analyse_one(spec, builder.build(spec), graph.get(spec.id, {}), req)
                result.specs.append(uc)
                result.calls += 1
            except (LLMError, ValueError, json.JSONDecodeError) as e:
                write(f"  ! {spec.id}: {e}")
                result.failed.append(spec.id)
                result.specs.append(self._fallback_spec(spec, graph.get(spec.id, {}), req))
        result.html = render_ucs_html(self.project.cfg, result.specs, req)
        result.document_id = self.project.store.save_ucs(
            req.mode, req.endpoint_id or "", req.new_requirement, req.previous_analysis,
            result.html, {"use_cases": [s.to_dict() for s in result.specs], "failed": result.failed},
        )
        result.seconds = time.time() - t0
        write(f"analyst done: {len(result.specs)} use cases, {result.calls} calls ({result.seconds:.1f}s)")
        return result

    def _analyse_one(self, spec: EndpointSpec, doc, graph: Dict[str, Any], req: AnalystRequest) -> UseCaseSpec:
        unit = self.analyzer.units.endpoint_unit(spec, doc)
        extra = self._context(req, spec, graph)
        user = build_user_prompt(
            [{"id": spec.id, "kind": "usecase", "name": spec.id, "path": spec.path, "method": spec.http_method}],
            [unit.payload],
            extra,
        )
        assert self.client is not None
        resp = self.client.complete(SYSTEM_PROMPT, user, json_mode=True)
        data = extract_json(resp.text)
        payload = data.get("results", data)
        if spec.id in payload and isinstance(payload[spec.id], dict):
            payload = payload[spec.id]
        elif not isinstance(payload, dict) or "name" not in payload and "use_case_id" not in payload:
            if isinstance(payload, dict) and len(payload) == 1:
                only = next(iter(payload.values()))
                if isinstance(only, dict):
                    payload = only
        uc = UseCaseSpec.from_dict(payload if isinstance(payload, dict) else {}, spec.id)
        uc.endpoint_id = spec.id
        if not uc.related_methods:
            uc.related_methods = [m["qname"] for m in graph.get("methods", [])]
        if req.new_requirement and not uc.new_requirement_impacts:
            uc.new_requirement_impacts = [req.new_requirement]
        return uc

    def _fallback_spec(self, spec: EndpointSpec, graph: Dict[str, Any], req: AnalystRequest) -> UseCaseSpec:
        title = spec.summary or spec.path
        uc = UseCaseSpec(
            use_case_id=f"UC-{spec.http_method}-{spec.path.strip('/').replace('/', '-')}",
            name=title or spec.id,
            endpoint_id=spec.id,
            actors=["کاربر سامانه", "سامانه"],
            description=spec.description or spec.summary or spec.id,
            trigger=f"{spec.http_method} {spec.path}",
            related_methods=[m["qname"] for m in graph.get("methods", [])],
        )
        uc.main_flow = []
        if req.new_requirement:
            uc.special_requirements = [req.new_requirement]
            uc.new_requirement_impacts = [req.new_requirement]
        return uc

    @staticmethod
    def _context(req: AnalystRequest, spec: EndpointSpec, graph: Dict[str, Any]) -> str:
        lines = [
            f"HTTP: {spec.http_method} {spec.path}",
            f"Handler: {spec.handler_qname}",
            "Called methods (controller-rooted):",
        ]
        for m in graph.get("methods", [])[:40]:
            lines.append(f"  - {m.get('qname')} (depth {m.get('depth')})")
        if req.previous_analysis.strip():
            lines.append("Previous analysis (preserve unless the new requirement or current code contradicts it):\n" + req.previous_analysis.strip())
        if req.new_requirement.strip():
            lines.append("NEW REQUIREMENT — mark impacted steps/rules with is_new=true:\n" + req.new_requirement.strip())
        return "\n".join(lines)
