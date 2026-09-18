"""High-level operations shared by the CLI and the web UI."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .analysis.analyzer import Analyzer
from .archive import Archive
from .config import Config
from .detectors.model import EndpointSpec
from .docmodel.model import EndpointDoc, FieldRow
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
    started = time.time()
    info = scan_project(project, log, force=force)
    stats = project.store.graph_stats()
    info.update({
        "files": stats["files"],
        "symbols": stats["symbols"],
        "edges": stats["edges"],
        "seconds": round(time.time() - started, 2),
    })
    return info


def do_analyze(
    project: Project,
    client: Optional[LLMClient],
    log: Log = _noop,
    dry_run: bool = False,
    only: Optional[List[str]] = None,
    prune: bool = False,
) -> Dict[str, Any]:
    analyzer = Analyzer(project, client)
    plan = analyzer.plan(only)
    summary = plan.summary()
    log("analysis plan: " + ", ".join(f"{key}={value}" for key, value in summary.items()))
    for miss in plan.misses:
        log(
            f"  needs analysis: [{miss.unit.kind}] {miss.unit.unit_id} "
            f"(~{miss.unit.tokens} tokens)"
        )
    report = analyzer.run(plan, progress=log, dry_run=dry_run)
    if prune and not dry_run:
        pruned = analyzer.prune_cache(plan)
        log(f"pruned {pruned} stale cache entries")

    result = {
        "plan": summary,
        "calls": report.calls,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "cache_read_tokens": report.cache_read_tokens,
        "cache_write_tokens": report.cache_write_tokens,
        "analysed_units": report.analysed_units,
        "failed_units": report.failed_units,
        "stopped": report.stopped_reason,
        "seconds": round(report.duration_s, 2),
    }
    if not dry_run and client is not None:
        stopped = f", stopped: {report.stopped_reason}" if report.stopped_reason else ""
        log(
            f"analysis: {report.calls} calls, "
            f"{report.input_tokens} input / {report.output_tokens} output tokens, "
            f"{report.analysed_units} units analysed{stopped}"
        )
    return result


def do_ucs(
    project: Project,
    client: Optional[LLMClient],
    log: Log = _noop,
    mode: str = "new",
    endpoint_id: Optional[str] = None,
    previous_analysis: str = "",
    new_requirement: str = "",
    dry_run: bool = False,
    out: Optional[str] = None,
) -> Dict[str, Any]:
    from .agent import AnalystRequest, SeniorAnalyst

    analyst = SeniorAnalyst(project, client)
    request = AnalystRequest(
        mode=mode,
        endpoint_id=endpoint_id,
        previous_analysis=previous_analysis,
        new_requirement=new_requirement,
        dry_run=dry_run,
    )
    result = analyst.run(request, log)
    out_path = Path(out) if out else (project.cfg.root / "docs" / "use-case-specifications.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.html, encoding="utf-8")
    log(f"use-case document -> {out_path}")
    return {
        "output": str(out_path),
        "document_id": result.document_id,
        "use_cases": [spec.to_dict() for spec in result.specs],
        "calls": result.calls,
        "failed": result.failed,
        "seconds": round(result.seconds, 2),
        "scan": {key: value for key, value in result.scan.items() if key != "controller_graph"},
        "html": result.html,
    }


def build_docs(
    project: Project,
    client: Optional[LLMClient] = None,
    only: Optional[List[str]] = None,
):
    analyzer = Analyzer(project, client)
    specs = analyzer.select_specs(only)
    docs: List[EndpointDoc] = []
    analyses: Dict[str, Dict[str, Any]] = {}
    for number, spec in enumerate(specs, 1):
        doc = analyzer.build_doc(spec, number)
        docs.append(doc)
        _types, endpoint_analysis, _status = analyzer.results_for(spec, doc)
        analyses[spec.id] = endpoint_analysis
    return analyzer, specs, docs, analyses


def do_render(
    project: Project,
    log: Log = _noop,
    out: Optional[str] = None,
    only: Optional[List[str]] = None,
    archive: bool = True,
    label: str = "",
) -> Dict[str, Any]:
    started = time.time()
    analyzer, specs, docs, analyses = build_docs(project, None, only)
    renderer = HtmlRenderer(project.cfg, project.store)
    if project.cfg.get("doc", "intro_llm", default=False):
        renderer.intro_override = analyzer.intro_html(analyzer.plan(only))

    configured = project.cfg.resolve_path(project.cfg.get("doc", "output"))
    out_path = Path(out) if out else (configured or Path("api-document.html"))
    renderer.render_to_file(docs, out_path, analyses)

    status_counts: Dict[str, int] = {}
    for doc in docs:
        status_counts[doc.analysis_status] = status_counts.get(doc.analysis_status, 0) + 1
    size_kb = out_path.stat().st_size // 1024
    log(
        f"rendered {len(docs)} endpoints -> {out_path} ({size_kb} KB); "
        f"analysis status: {status_counts}"
    )

    archive_entry = None
    if archive:
        meta = renderer.build_meta(docs)
        archive_entry = Archive(project.cfg).add(
            out_path, docs, meta, usage=project.store.usage_totals(), label=label,
        )
        log(
            f"archived as {archive_entry['file']} "
            f"(added {len(archive_entry['added'])}, "
            f"changed {len(archive_entry['changed'])}, "
            f"removed {len(archive_entry['removed'])})"
        )

    json_path = out_path.with_suffix(".json")
    payload = {"endpoints": [endpoint_doc_summary(doc) for doc in docs]}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "output": str(out_path),
        "endpoints": len(docs),
        "status": status_counts,
        "archive": archive_entry,
        "seconds": round(time.time() - started, 2),
    }


def _field_row_summary(row: FieldRow) -> Dict[str, Any]:
    return {
        "name": row.name,
        "type": row.type_str,
        "required": row.required,
        "description": row.description,
        "location": row.location,
        "nested": row.nested_qname,
        "enum": row.enum_qname,
    }


def endpoint_doc_summary(doc: EndpointDoc) -> Dict[str, Any]:
    return {
        "id": doc.spec.id,
        "number": doc.number,
        "title": doc.title,
        "description": doc.description,
        "method": doc.http_method,
        "path": doc.spec.path,
        "address": doc.address,
        "framework": doc.spec.framework,
        "handler": doc.spec.handler_qname,
        "file": doc.spec.file_path,
        "anchor": doc.anchor,
        "analysis_status": doc.analysis_status,
        "headers": [_field_row_summary(row) for row in doc.header_rows],
        "request": [_field_row_summary(row) for row in doc.request_rows],
        "response": [_field_row_summary(row) for row in doc.response_rows],
        "nested_types": [
            {
                "qname": table.qname,
                "name": table.name,
                "side": table.side,
                "description": table.description,
                "fields": [_field_row_summary(row) for row in table.fields],
            }
            for table in doc.nested_tables
        ],
        "enums": {
            qname: [
                {"name": value.name, "label": value.label, "code": value.code, "wire": value.wire}
                for value in enum.values
            ]
            for qname, enum in doc.enum_tables.items()
        },
        "notes": doc.notes,
        "error_codes": [
            {"code": error.code, "title": error.title, "note": error.note, "source": error.source}
            for error in doc.error_rows
        ],
        "success_sample": doc.success_sample,
        "failure_samples": doc.failure_samples,
        "auth_text": doc.auth_text,
        "business_rules": doc.business_rules,
        "failure_rows": [_field_row_summary(row) for row in doc.failure_rows],
        "type_closure": doc.type_closure,
    }


def status_info(project: Project) -> Dict[str, Any]:
    store = project.store
    llm_cfg = dict(project.cfg.get("llm", default={}) or {})
    llm_cfg.pop("api_key", None)
    return {
        "config": str(project.cfg.path),
        "db": str(project.cfg.db_path),
        "graph": store.graph_stats(),
        "cache": store.cache_stats(),
        "usage": store.usage_totals(),
        "usage_by_model": store.usage_by_model(),
        "last_scan_at": store.get_meta("last_scan_at"),
        "last_scan_seconds": store.get_meta("last_scan_seconds"),
        "parse_errors": store.files_with_errors(),
        "llm": llm_cfg,
    }


def endpoint_tree(specs: List[EndpointSpec]) -> Dict[str, Any]:
    """Nest endpoints by path segment for the UI."""
    root: Dict[str, Any] = {"name": "/", "children": {}, "endpoints": []}
    for spec in specs:
        node = root
        for segment in [part for part in spec.path.split("/") if part]:
            node = node["children"].setdefault(
                segment, {"name": segment, "children": {}, "endpoints": []},
            )
        node["endpoints"].append({
            "id": spec.id,
            "method": spec.http_method,
            "path": spec.path,
            "framework": spec.framework,
            "handler": spec.handler_qname,
            "summary": spec.summary[:120],
            "deprecated": spec.deprecated,
        })

    def to_list(node: Dict[str, Any]) -> Dict[str, Any]:
        children = [to_list(child) for _, child in sorted(node["children"].items())]
        return {"name": node["name"], "endpoints": node["endpoints"], "children": children}

    return to_list(root)


def _handler_qnames(spec: EndpointSpec) -> List[str]:
    names = [spec.handler_qname]
    if spec.impl_qname:
        names.append(spec.impl_qname)
    return names


def _reachable_callees(store: GraphStore, roots: List[str], depth: int) -> List[str]:
    """Follow ``calls`` edges from the handler, staying inside the project."""
    frontier = list(roots)
    seen = set(roots)
    for _ in range(depth):
        next_frontier = []
        for method_qname in frontier:
            for dest, _kind, _meta in store.edges_from(method_qname, "calls"):
                if dest.startswith("ext:") or dest in seen:
                    continue
                seen.add(dest)
                next_frontier.append(dest)
        frontier = next_frontier
    return list(seen)


def _files_for_symbols(store: GraphStore, qnames: List[str], changed_files: set) -> List[str]:
    touched: List[str] = []
    for qname in qnames:
        symbol = store.get_symbol(qname)
        if symbol is None:
            continue
        resolved = str(Path(symbol.file_path).resolve())
        if resolved in changed_files and symbol.file_path not in touched:
            touched.append(symbol.file_path)
    return touched


def impact(project: Project, files: List[str]) -> Dict[str, List[str]]:
    """Which endpoints would need re-analysis if these source files change."""
    analyzer = Analyzer(project, None)
    changed_files = {str(Path(path).resolve()) for path in files}
    affected: Dict[str, List[str]] = {}
    store = project.store
    call_depth = int(project.cfg.get("analysis", "call_depth", default=1)) + 1

    for spec in analyzer.select_specs():
        doc = analyzer.doc_builder.build(spec)
        roots = _handler_qnames(spec)
        related = roots + doc.type_closure + _reachable_callees(store, roots, call_depth)
        touched = _files_for_symbols(store, related, changed_files)
        if touched:
            affected[spec.id] = touched
    return affected
