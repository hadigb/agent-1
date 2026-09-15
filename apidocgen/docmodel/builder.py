"""Builds the per-endpoint document model from the graph, deterministic
extraction, cached LLM analyses and user overrides."""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Set

from ..config import Config
from ..detectors.model import EndpointSpec
from ..graph.index import CodeIndex
from ..graph.store import GraphStore
from ..javaparse.model import TypeRef
from .errors import ErrorCatalog, ErrorRow, collect_endpoint_errors, merge_error_rows
from .fields import FieldExpander
from .model import EndpointDoc, EnumTable, FieldRow, TypeTable
from .samples import build_samples, request_sample

DEFAULT_CALL_STEPS = """<p>1- با استفاده از داده‌های موجود، رشته زیر را ایجاد نمایید. آیتم‌های موجود در این رشته با علامت # از یکدیگر جدا شده‌اند.</p>
<p class="ltr mono">httpMethod(Upper Case)#url(after Address Root)#ApiKey#body</p>
<p>نمونه رشته بالا:</p>
<p class="ltr mono wrap">{method}#{path}#a8b40f20-da83-45ee-bf46-c1e4a9c717bf#{body}</p>
<p>2- Sign کردن رشته بالا با Private Key به روش SHA1 و تبدیل خروجی نهایی به Base64 (توجه داشته باشید که عبارت بالا به صورت یک رشته کامل و بدون Space باید امضا گردد).</p>
<p>3- اضافه کردن Apikey و رشته sign شده به Header درخواست با کلیدهای زیر: ApiKey و Signature</p>
<p>4- اضافه کردن Body به بدنه درخواست (در صورتی که نوع متد Get باشد این مقدار خالی می‌باشد).</p>
<p>5- ارسال درخواست به آدرس ذکر شده در بالای صفحه. توجه داشته باشید که ContentType درخواست باید application/json باشد.</p>
"""


def slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "endpoint"


class DocBuilder:
    def __init__(self, cfg: Config, index: CodeIndex, store: GraphStore) -> None:
        self.cfg = cfg
        self.index = index
        self.store = store
        self.expander = FieldExpander(index)
        self.catalog = ErrorCatalog(index, cfg.get("analysis", "error_catalog_regex", default="") or "")
        self.envelope = {
            "success": cfg.get("analysis", "envelope_success_fields", default=[]),
            "code": cfg.get("analysis", "envelope_code_fields", default=[]),
            "message": cfg.get("analysis", "envelope_message_fields", default=[]),
            "errors": cfg.get("analysis", "envelope_errors_fields", default=[]),
            "data": cfg.get("analysis", "envelope_data_fields", default=[]),
        }
        self.call_steps_template = self._load_call_steps()

    def _load_call_steps(self) -> str:
        if not self.cfg.get("doc", "show_call_steps", default=False):
            return ""
        p = self.cfg.resolve_path(self.cfg.get("doc", "call_steps_file"))
        if p and p.exists():
            text = p.read_text(encoding="utf-8")
            if p.suffix.lower() in (".md", ".markdown"):
                try:
                    import markdown  # type: ignore

                    return markdown.markdown(text)
                except Exception:  # pragma: no cover
                    return "<pre>" + html.escape(text) + "</pre>"
            return text
        return DEFAULT_CALL_STEPS

    # ------------------------------------------------------------------ main
    def build(self, spec: EndpointSpec, number: int = 0, type_analyses: Optional[Dict[str, Dict[str, Any]]] = None,
              endpoint_analysis: Optional[Dict[str, Any]] = None, analysis_status: str = "none") -> EndpointDoc:
        type_analyses = type_analyses or {}
        endpoint_analysis = endpoint_analysis or {}
        overrides = (self.cfg.get("overrides", default={}) or {}).get(spec.id, {}) or {}
        ctx = self.index.context_for(spec.type_qname) if spec.type_qname in self.index.types else None
        doc = EndpointDoc(spec=spec, number=number, http_method=spec.http_method,
                          address=(self.cfg.get("doc", "base_url", default="") or "").rstrip("/") + spec.path,
                          anchor=slugify(spec.id), analysis_status=analysis_status)
        # ---- title / description
        doc.title = (overrides.get("title") or endpoint_analysis.get("title")
                     or self._default_title(spec))
        doc.description = (overrides.get("description") or endpoint_analysis.get("description")
                           or endpoint_analysis.get("summary") or spec.description or spec.summary or "")
        # ---- headers
        doc.header_rows = self._header_rows(spec, endpoint_analysis)
        # ---- request
        tables: List[TypeTable] = []
        enums: Dict[str, EnumTable] = {}
        visited: Set[str] = set()
        req_rows: List[FieldRow] = []
        for p in spec.params:
            if p.location in ("header", "body"):
                continue
            if p.location == "query-object" and ctx is not None:
                rows, _ = self.expander.expand(p.type, ctx, "request", visited, tables, enums)
                for r in rows:
                    r.location = "query"
                    r.description = r.code_description or p.description
                req_rows.extend(rows)
                continue
            type_str, nested, enum, is_list = self.expander.classify(p.type, ctx) if ctx else (p.type.canonical(), None, None, False)
            if enum and enum not in enums:
                enums[enum] = self.expander.enum_table(enum)
            row = FieldRow(name=p.name, java_name=p.java_name, type_str=type_str, required=p.required,
                           code_description=p.description, description=p.description, location=p.location,
                           default=p.default, enum_qname=enum, nested_qname=nested, is_list=is_list,
                           java_type=p.type.canonical())
            if p.default is not None and p.default != "":
                row.description = (row.description + f" (مقدار پیش‌فرض: {p.default})").strip()
            req_rows.append(row)
        body_root: Optional[str] = None
        if spec.body_type is not None and ctx is not None:
            rows, body_root = self.expander.expand(spec.body_type, ctx, "request", visited, tables, enums)
            req_rows.extend(rows)
        doc.request_rows = req_rows
        doc.request_type_name = self.index.types[body_root].name if body_root else (spec.body_type.canonical() if spec.body_type else "")
        # ---- response
        resp_root: Optional[str] = None
        if spec.response_type is not None and ctx is not None:
            resp_visited: Set[str] = set()
            resp_tables: List[TypeTable] = []
            rows, resp_root = self.expander.expand(spec.response_type, ctx, "response", resp_visited, resp_tables, enums)
            doc.response_rows = rows
            for t in resp_tables:
                if t.qname not in {x.qname for x in tables}:
                    tables.append(t)
            visited |= resp_visited
        doc.response_type_name = self.index.types[resp_root].name if resp_root else (spec.response_type.canonical() if spec.response_type else "")
        doc.nested_tables = tables
        doc.enum_tables = enums
        doc.type_closure = [q for q in [body_root, resp_root] if q] + [t.qname for t in tables] + list(enums.keys())
        # ---- apply LLM analyses to rows
        self._apply_type_analyses(doc, body_root, resp_root, type_analyses)
        self._apply_param_analysis(doc, endpoint_analysis)
        # ---- notes
        notes: List[str] = list(spec.notes)
        notes.extend(endpoint_analysis.get("notes", []) or [])
        if overrides.get("notes") is not None:
            notes = list(overrides["notes"])
        notes.extend(overrides.get("extra_notes", []) or [])
        doc.notes = [n for n in notes if n]
        # ---- error codes
        handlers = [spec.handler_qname] + ([spec.impl_qname] if spec.impl_qname else [])
        code_rows = collect_endpoint_errors(self.store, self.catalog, handlers,
                                            depth=int(self.cfg.get("analysis", "call_depth", default=1)) + 2)
        llm_rows = [ErrorRow(code=str(e.get("code", "")), title=e.get("title", ""), note=e.get("note", ""), source="llm")
                    for e in (endpoint_analysis.get("error_codes", []) or []) if e.get("code")]
        ov_rows = [ErrorRow(code=str(e.get("code", "")), title=e.get("title", ""), note=e.get("note", ""), source="override")
                   for e in (overrides.get("error_codes", []) or []) if e.get("code")]
        doc.error_rows = merge_error_rows(ov_rows, code_rows, llm_rows,
                                         success_first=self.cfg.get("doc", "success_code"),
                                         always_last=self.cfg.get("doc", "common_error_codes", default=[]) or [])
        # ---- field overrides
        for name, desc in (overrides.get("fields", {}) or {}).items():
            for r in self._all_rows(doc):
                if r.name == name or r.java_name == name:
                    r.description = desc
        hide = set(overrides.get("hide_fields", []) or [])
        if hide:
            doc.request_rows = [r for r in doc.request_rows if r.name not in hide]
            doc.response_rows = [r for r in doc.response_rows if r.name not in hide]
        # ---- samples
        self._build_samples(doc, overrides)
        # ---- call steps
        body_sample = request_sample(doc.request_rows, doc.nested_tables, doc.enum_tables)
        steps = self.call_steps_template
        for key, val in (("{method}", html.escape(spec.http_method.split("/")[0])), ("{path}", html.escape(spec.path)),
                         ("{body}", html.escape(body_sample)), ("{api_key}", "a8b40f20-da83-45ee-bf46-c1e4a9c717bf")):
            steps = steps.replace(key, val)
        doc.call_steps_html = steps
        return doc

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _all_rows(doc: EndpointDoc):
        for r in doc.header_rows + doc.request_rows + doc.response_rows:
            yield r
        for t in doc.nested_tables:
            for r in t.fields:
                yield r

    def _default_title(self, spec: EndpointSpec) -> str:
        last = spec.path.rstrip("/").rsplit("/", 1)[-1] or spec.path
        last = re.sub(r"[{}]", "", last)
        return f"سرویس {last}"

    def _header_rows(self, spec: EndpointSpec, endpoint_analysis: Dict[str, Any]) -> List[FieldRow]:
        rows: List[FieldRow] = []
        seen: Dict[str, FieldRow] = {}
        for h in self.cfg.get("doc", "common_headers", default=[]) or []:
            r = FieldRow(name=h["name"], java_name=h["name"], type_str=h.get("type", "String"),
                         required=bool(h.get("required", False)), description=h.get("description", ""),
                         code_description=h.get("description", ""), location="header")
            rows.append(r)
            seen[r.name.lower()] = r
        for p in spec.params:
            if p.location != "header":
                continue
            key = p.name.lower()
            if key in seen:
                r = seen[key]
                if p.description and not r.description:
                    r.description = p.description
                r.java_name = p.java_name
                continue
            r = FieldRow(name=p.name, java_name=p.java_name, type_str="String", required=p.required,
                         description=p.description, code_description=p.description, location="header",
                         default=p.default, java_type=p.type.canonical())
            rows.append(r)
            seen[key] = r
        return rows

    def _apply_type_analyses(self, doc: EndpointDoc, body_root: Optional[str], resp_root: Optional[str],
                             analyses: Dict[str, Dict[str, Any]]) -> None:
        prefer_code = bool(self.cfg.get("analysis", "prefer_code_comments", default=False))

        def apply(rows: List[FieldRow], owner: Optional[str]) -> None:
            for r in rows:
                a = analyses.get(r.owner_qname or owner or "")
                if not a:
                    continue
                f = (a.get("fields") or {}).get(r.java_name) or (a.get("fields") or {}).get(r.name)
                if not f:
                    continue
                if isinstance(f, str):
                    f = {"description": f}
                desc = f.get("description")
                if desc and (not prefer_code or not r.code_description):
                    r.description = desc
                if f.get("example") not in (None, ""):
                    r.example = f.get("example")
                if f.get("required") is True and not r.required:
                    r.required = True

        apply(doc.request_rows, body_root)
        apply(doc.response_rows, resp_root)
        for t in doc.nested_tables:
            apply(t.fields, t.qname)
            a = analyses.get(t.qname)
            if a and a.get("description") and (not prefer_code or not t.description):
                t.description = a["description"]
        for q, e in doc.enum_tables.items():
            a = analyses.get(q)
            if not a:
                continue
            labels = a.get("enum_values") or {}
            for v in e.values:
                if v.name in labels and labels[v.name] and (not v.label or not prefer_code):
                    v.label = labels[v.name]
            if a.get("description") and (not prefer_code or not e.title):
                e.title = a["description"]

    def _apply_param_analysis(self, doc: EndpointDoc, analysis: Dict[str, Any]) -> None:
        prefer_code = bool(self.cfg.get("analysis", "prefer_code_comments", default=False))

        def apply(rows, descriptions, owner=None):
            for r in rows:
                # Nested fields require a qualified key so unrelated id/name fields do not collide.
                keys = ([f"{owner}.{r.name}", f"{owner}.{r.java_name}"] if owner else [r.name, r.java_name])
                d = next((descriptions[k] for k in keys if descriptions.get(k)), None)
                if isinstance(d, dict):
                    d = d.get("description")
                # An endpoint-specific explanation is more informative than a shared DTO label.
                if d and (not prefer_code or not r.code_description):
                    r.description = d

        params = analysis.get("params") or {}
        response = analysis.get("response_params") or {}
        apply(doc.header_rows + doc.request_rows, params)
        apply(doc.response_rows, response)
        for table in doc.nested_tables:
            apply(table.fields, params if table.side == "request" else response, table.qname)

    def _build_samples(self, doc: EndpointDoc, overrides: Dict[str, Any]) -> None:
        if not self.cfg.get("doc", "show_samples", default=True):
            return
        samples_ov = overrides.get("samples") or {}
        first_required = next((r.name for r in doc.request_rows if r.required and r.location == "body"), None)
        validation = self.cfg.get("doc", "validation_error", default=None)
        if not validation:
            validation = {"code": "400", "title": "درخواست نامعتبر است"}
        business = None
        success_cfg = self.cfg.get("doc", "success_code", default=None) or {}
        success_code = str(success_cfg.get("code", ""))
        for e in doc.error_rows:
            if e.code not in (success_code, str(validation.get("code")), str(validation.get("item_code"))) and e.source == "code":
                business = {"code": int(e.code) if e.code.isdigit() else e.code, "title": e.title}
                break
        if doc.response_rows:
            success, failures = build_samples(doc.response_rows, doc.nested_tables, doc.enum_tables, self.envelope,
                                              success_cfg, validation,
                                              business, first_required)
            doc.success_sample = success
            doc.failure_samples = failures
        if samples_ov.get("success"):
            doc.success_sample = samples_ov["success"]
        if samples_ov.get("failures"):
            doc.failure_samples = list(samples_ov["failures"])
