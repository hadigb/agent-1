"""Scan orchestration: hash-stable file records and controller-rooted call graphs."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Set

from ..graph.store import GraphStore
from ..project import Project

Log = Callable[[str], None]


def build_controller_graph(project: Project, depth: int = 8) -> List[Dict[str, Any]]:
    """Walk calls starting from each detected controller/handler method."""
    store = project.store
    out: List[Dict[str, Any]] = []
    for spec in project.endpoints():
        roots = [spec.handler_qname] + ([spec.impl_qname] if spec.impl_qname else [])
        methods = _walk_methods(store, [r for r in roots if r], depth)
        out.append({
            "id": spec.id,
            "method": spec.http_method,
            "path": spec.path,
            "handler": spec.handler_qname,
            "file": spec.file_path,
            "methods": methods,
        })
    return out


def _walk_methods(store: GraphStore, roots: List[str], depth: int) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    ordered: List[Dict[str, Any]] = []
    frontier: List[tuple] = [(r, 0) for r in roots]
    while frontier:
        mq, d = frontier.pop(0)
        if mq in seen or mq.startswith("ext:"):
            continue
        seen.add(mq)
        sym = store.get_symbol(mq)
        ordered.append({
            "qname": mq,
            "name": (sym.name if sym else mq.split("#")[-1]),
            "file": (sym.file_path if sym else ""),
            "line": (sym.start_line if sym else 0),
            "depth": d,
        })
        if d >= depth:
            continue
        for dst, _kind, _meta in store.edges_from(mq, "calls"):
            if dst.startswith("ext:") or dst in seen:
                continue
            frontier.append((dst, d + 1))
    return ordered


def scan_project(project: Project, log: Optional[Log] = None, force: bool = False) -> Dict[str, Any]:
    write = log or (lambda _: None)
    result = project.scan(progress=lambda p: write(f"  parsed {p}"), force=force)
    graph = build_controller_graph(project)
    project.store.set_meta("controller_graph", json.dumps(graph, ensure_ascii=False))
    info = {
        "added": len(result.added),
        "changed": len(result.changed),
        "removed": len(result.removed),
        "unchanged": result.unchanged,
        "parse_errors": result.parse_errors,
        "endpoints": len(project.endpoints()),
        "controller_graph": graph,
    }
    write(
        f"scan: {info['added']} added, {info['changed']} changed, {info['removed']} removed, "
        f"{info['unchanged']} unchanged (hash match, not re-saved); "
        f"{info['endpoints']} endpoints, {sum(len(g['methods']) for g in graph)} controller-rooted methods"
    )
    for path, err in result.parse_errors.items():
        write(f"  parse warning {path}: {err[:200]}")
    return info
