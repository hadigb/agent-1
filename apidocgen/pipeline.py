"""High level operations shared by the CLI and the web UI."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .analysis.analyzer import AnalysisPlan, AnalysisReport, Analyzer
from .archive import Archive
from .config import Config
from .detectors.model import EndpointSpec
from .docmodel.model import EndpointDoc
from .graph.store import GraphStore
from .llm.base import LLMClient
from .llm.providers import make_client
from .project import Project
from .render.html import HtmlRenderer
from .services.scan_service import scan_project

Log = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def make_llm(cfg: Config, override_provider: Optional[str] = None) -> LLMClient:
    llm_cfg = dict(cfg.get("llm", default={}) or {})
    if override_provider:
        llm_cfg["provider"] = override_provider
    return make_client(llm_cfg, cfg.llm_api_key())


def do_scan(project: Project, log: Log = _noop, force: bool = False) -> Dict[str, Any]:
    t0 = time.time()
    info = scan_project(project, log, force=force)
    stats = project.store.graph_stats()
    info.update({"files": stats["files"], "symbols": stats["symbols"], "edges": stats["edges"],
                 "seconds": round(time.time() - t0, 2)})
    return info


def do_analyze(project: Project, client: Optional[LLMClient], log: Log = _noop, dry_run: bool = False,
               only: Optional[List[str]] = None, prune: bool = False) -> Dict[str, Any]:
    an = Analyzer(project, client)
    plan = an.plan(only)
    summary = plan.summary()
    log("analysis plan: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    for u in plan.misses:
        log(f"  needs analysis: [{u.unit.kind}] {u.unit.unit_id} (~{u.unit.tokens} tokens)")
    report = an.run(plan, progress=log, dry_run=dry_run)
    if prune and not dry_run:
        n = an.prune_cache(plan)
        log(f"pruned {n} stale cache entries")
    out = {"plan": summary, "calls": report.calls, "input_tokens": report.input_tokens, "output_tokens": report.output_tokens,
           "cache_read_tokens": report.cache_read_tokens, "cache_write_tokens": report.cache_write_tokens,
           "analysed_units": report.analysed_units, "failed_units": report.failed_units, "stopped": report.stopped_reason,
           "seconds": round(report.duration_s, 2)}
    if not dry_run and client is not None:
        log(f"analysis: {report.calls} calls, {report.input_tokens} input / {report.output_tokens} output tokens, "
            f"{report.analysed_units} units analysed" + (f", stopped: {report.stopped_reason}" if report.stopped_reason else ""))
    return out


def do_ucs(project: Project, client: Optional[LLMClient], log: Log = _noop, mode: str = "new",
           endpoint_id: Optional[str] = None, previous_analysis: str = "", new_requirement: str = "",
           dry_run: bool = False, out: Optional[str] = None) -> Dict[str, Any]:
    from .agent import AnalystRequest, SeniorAnalyst

    analyst = SeniorAnalyst(project, client)
    result = analyst.run(AnalystRequest(mode=mode, endpoint_id=endpoint_id, previous_analysis=previous_analysis,
                                        new_requirement=new_requirement, dry_run=dry_run), log)
    out_path = Path(out) if out else (project.cfg.root / "docs" / "use-case-specifications.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.html, encoding="utf-8")
    log(f"use-case document -> {out_path}")
    return {
        "output": str(out_path),
        "document_id": result.document_id,
        "use_cases": [s.to_dict() for s in result.specs],
        "calls": result.calls,
        "failed": result.failed,
        "seconds": round(result.seconds, 2),
        "scan": {k: v for k, v in result.scan.items() if k != "controller_graph"},
        "html": result.html,
    }


def build_docs(project: Project, client: Optional[LLMClient] = None, only: Optional[List[str]] = None):
    an = Analyzer(project, client)
    specs = an.select_specs(only)
    docs: List[EndpointDoc] = []
    analyses: Dict[str, Dict[str, Any]] = {}
    for i, s in enumerate(specs, 1):
        d = an.build_doc(s, i)
        docs.append(d)
        _t, ep, _st = an.results_for(s, d)
        analyses[s.id] = ep
    return an, specs, docs, analyses


def do_render(project: Project, log: Log = _noop, out: Optional[str] = None, only: Optional[List[str]] = None,
              archive: bool = True, label: str = "") -> Dict[str, Any]:
    t0 = time.time()
    an, specs, docs, analyses = build_docs(project, None, only)
    renderer = HtmlRenderer(project.cfg, project.store)
    if project.cfg.get("doc", "intro_llm", default=False):
        renderer.intro_override = an.intro_html(an.plan(only))
    out_path = Path(out) if out else (project.cfg.resolve_path(project.cfg.get("doc", "output")) or Path("api-document.html"))
    renderer.render_to_file(docs, out_path, analyses)
    status_counts: Dict[str, int] = {}
    for d in docs:
        status_counts[d.analysis_status] = status_counts.get(d.analysis_status, 0) + 1
    log(f"rendered {len(docs)} endpoints -> {out_path} ({out_path.stat().st_size // 1024} KB); analysis status: {status_counts}")
    entry = None
    if archive:
        meta = renderer.build_meta(docs)
        entry = Archive(project.cfg).add(out_path, docs, meta, usage=project.store.usage_totals(), label=label)
        log(f"archived as {entry['file']} (added {len(entry['added'])}, changed {len(entry['changed'])}, removed {len(entry['removed'])})")
    # machine readable companion
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps({"endpoints": [endpoint_doc_summary(d) for d in docs]}, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    return {"output": str(out_path), "endpoints": len(docs), "status": status_counts, "archive": entry,
            "seconds": round(time.time() - t0, 2)}


def endpoint_doc_summary(d: EndpointDoc) -> Dict[str, Any]:
    def rows(rs):
        return [{"name": r.name, "type": r.type_str, "required": r.required, "description": r.description,
                 "location": r.location, "nested": r.nested_qname, "enum": r.enum_qname} for r in rs]
    return {
        "id": d.spec.id, "number": d.number, "title": d.title, "description": d.description, "method": d.http_method,
        "path": d.spec.path, "address": d.address, "framework": d.spec.framework, "handler": d.spec.handler_qname,
        "file": d.spec.file_path, "anchor": d.anchor, "analysis_status": d.analysis_status,
        "headers": rows(d.header_rows), "request": rows(d.request_rows), "response": rows(d.response_rows),
        "nested_types": [{"qname": t.qname, "name": t.name, "side": t.side, "description": t.description, "fields": rows(t.fields)}
                         for t in d.nested_tables],
        "enums": {q: [{"name": v.name, "label": v.label, "code": v.code} for v in e.values] for q, e in d.enum_tables.items()},
        "notes": d.notes, "error_codes": [{"code": e.code, "title": e.title, "note": e.note, "source": e.source} for e in d.error_rows],
        "success_sample": d.success_sample, "failure_samples": d.failure_samples,
        "type_closure": d.type_closure,
    }


def status_info(project: Project) -> Dict[str, Any]:
    st = project.store
    return {
        "config": str(project.cfg.path), "db": str(project.cfg.db_path), "graph": st.graph_stats(),
        "cache": st.cache_stats(), "usage": st.usage_totals(), "usage_by_model": st.usage_by_model(),
        "last_scan_at": st.get_meta("last_scan_at"), "last_scan_seconds": st.get_meta("last_scan_seconds"),
        "parse_errors": st.files_with_errors(), "llm": {k: v for k, v in (project.cfg.get("llm", default={}) or {}).items()
                                                        if k not in ("api_key",)},
    }


def endpoint_tree(specs: List[EndpointSpec]) -> Dict[str, Any]:
    """Nest endpoints by path segment for the UI."""
    root: Dict[str, Any] = {"name": "/", "children": {}, "endpoints": []}
    for s in specs:
        node = root
        for seg in [x for x in s.path.split("/") if x]:
            node = node["children"].setdefault(seg, {"name": seg, "children": {}, "endpoints": []})
        node["endpoints"].append({"id": s.id, "method": s.http_method, "path": s.path, "framework": s.framework,
                                  "handler": s.handler_qname, "summary": s.summary[:120], "deprecated": s.deprecated})

    def to_list(n: Dict[str, Any]) -> Dict[str, Any]:
        return {"name": n["name"], "endpoints": n["endpoints"],
                "children": [to_list(c) for _k, c in sorted(n["children"].items())]}

    return to_list(root)


def impact(project: Project, files: List[str]) -> Dict[str, List[str]]:
    """Which endpoints would need re-analysis / re-documentation if these files change."""
    an = Analyzer(project, None)
    file_set = {str(Path(f).resolve()) for f in files}
    out: Dict[str, List[str]] = {}
    st = project.store
    for spec in an.select_specs():
        doc = an.doc_builder.build(spec)
        touched: List[str] = []
        syms = [spec.handler_qname] + ([spec.impl_qname] if spec.impl_qname else []) + doc.type_closure
        # walk calls up to depth
        frontier = [spec.handler_qname] + ([spec.impl_qname] if spec.impl_qname else [])
        seen = set(frontier)
        depth = int(project.cfg.get("analysis", "call_depth", default=1)) + 1
        for _ in range(depth):
            nxt = []
            for mq in frontier:
                for dst, kind, _m in st.edges_from(mq, "calls"):
                    if not dst.startswith("ext:") and dst not in seen:
                        seen.add(dst)
                        nxt.append(dst)
            frontier = nxt
        syms += list(seen)
        for q in syms:
            sym = st.get_symbol(q)
            if sym and str(Path(sym.file_path).resolve()) in file_set and sym.file_path not in touched:
                touched.append(sym.file_path)
        if touched:
            out[spec.id] = touched
    return out
