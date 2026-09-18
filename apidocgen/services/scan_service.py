"""Scan orchestration: hash-stable file records and controller-rooted call graphs."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..graph.store import GraphStore
from ..project import Project

Log = Callable[[str], None]


def build_controller_graph(project: Project, depth: int = 8) -> List[Dict[str, Any]]:
    """Walk calls starting from each detected controller/handler method."""
    store = project.store
    graph: List[Dict[str, Any]] = []
    for spec in project.endpoints():
        roots = [spec.handler_qname]
        if spec.impl_qname:
            roots.append(spec.impl_qname)
        methods = _walk_methods(store, [name for name in roots if name], depth)
        graph.append({
            "id": spec.id,
            "method": spec.http_method,
            "path": spec.path,
            "handler": spec.handler_qname,
            "file": spec.file_path,
            "methods": methods,
        })
    return graph


def _walk_methods(store: GraphStore, roots: List[str], depth: int) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    ordered: List[Dict[str, Any]] = []
    frontier: List[Tuple[str, int]] = [(root, 0) for root in roots]
    while frontier:
        method_qname, current_depth = frontier.pop(0)
        if method_qname in seen or method_qname.startswith("ext:"):
            continue
        seen.add(method_qname)
        symbol = store.get_symbol(method_qname)
        source = ""
        if symbol and isinstance(symbol.data, dict):
            source = str(symbol.data.get("source") or "")
        if not source and symbol:
            file_text = store.file_source(symbol.file_path) or ""
            if file_text and symbol.start_line:
                lines = file_text.splitlines()
                source = "\n".join(lines[max(0, symbol.start_line - 1):max(symbol.end_line, symbol.start_line)])
        ordered.append({
            "qname": method_qname,
            "name": (symbol.name if symbol else method_qname.split("#")[-1]),
            "file": (symbol.file_path if symbol else ""),
            "line": (symbol.start_line if symbol else 0),
            "depth": current_depth,
            "source": source,
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()[:12] if source else "",
        })
        if current_depth >= depth:
            continue
        for dest, _kind, _meta in store.edges_from(method_qname, "calls"):
            if dest.startswith("ext:") or dest in seen:
                continue
            frontier.append((dest, current_depth + 1))
    return ordered


def scan_project(project: Project, log: Optional[Log] = None, force: bool = False) -> Dict[str, Any]:
    write = log or (lambda _: None)
    result = project.scan(progress=lambda path: write(f"  parsed {path}"), force=force)
    graph = json.loads(project.store.get_meta("controller_graph") or "[]")
    info = {
        "added": len(result.added),
        "changed": len(result.changed),
        "removed": len(result.removed),
        "unchanged": result.unchanged,
        "parse_errors": result.parse_errors,
        "endpoints": len(project.endpoints()),
        "controller_graph": graph,
    }
    method_count = sum(len(entry["methods"]) for entry in graph)
    write(
        f"scan: {info['added']} added, {info['changed']} changed, {info['removed']} removed, "
        f"{info['unchanged']} unchanged (hash match, not re-saved); "
        f"{info['endpoints']} endpoints, {method_count} controller-rooted methods"
    )
    for path, error in result.parse_errors.items():
        write(f"  parse warning {path}: {error[:200]}")
    return info
