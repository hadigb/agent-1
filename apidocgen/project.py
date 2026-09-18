"""Project facade: config + store + index + endpoints."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

import yaml

from .config import Config
from .detectors import build_detectors, detect_endpoints
from .detectors.model import EndpointSpec
from .graph.builder import GraphBuilder
from .graph.index import CodeIndex
from .graph.store import EndpointRow, GraphStore
from .scanner import ScanResult, scan_files


class Project:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store = GraphStore(cfg.db_path)
        self._index: Optional[CodeIndex] = None
        self._index_stamp: Optional[str] = None  # last_scan_at when the cached index was built

    @property
    def index(self) -> CodeIndex:
        if self._index is None:
            stamp = self.store.get_meta("last_scan_at")
            self._index = CodeIndex(self.store.iter_parsed_files())
            self._index_stamp = stamp
        return self._index

    def invalidate_index(self) -> None:
        self._index = None
        self._index_stamp = None

    def manual_entries(self) -> List[Dict[str, Any]]:
        path = self.cfg.resolve_path(self.cfg.get("project", "endpoints_file"))
        if not path or not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return data.get("endpoints", []) or []

    def scan(self, progress: Optional[Callable[[str], None]] = None, force: bool = False) -> ScanResult:
        started = time.time()
        result = scan_files(self.cfg, self.store, progress=progress, force=force)
        self.store.set_meta("last_scan_at", str(time.time()))
        self.invalidate_index()
        self.rebuild_graph()
        self.store.set_meta("last_scan_seconds", f"{time.time() - started:.2f}")
        return result

    def rebuild_graph(self) -> List[EndpointSpec]:
        symbols, edges = GraphBuilder(self.index).build()
        detectors = build_detectors(self.cfg.data, self.manual_entries())
        specs = detect_endpoints(self.index, detectors)
        rows = [_endpoint_row(spec) for spec in specs]
        self.store.replace_graph(symbols, edges, rows)
        from .services.scan_service import build_controller_graph
        graph = build_controller_graph(self)
        self.store.set_meta("controller_graph", json.dumps(graph, ensure_ascii=False))
        return specs

    def endpoints(self) -> List[EndpointSpec]:
        return [EndpointSpec.from_dict(row.data) for row in self.store.list_endpoints()]

    def endpoint(self, endpoint_id: str) -> Optional[EndpointSpec]:
        row = self.store.get_endpoint(endpoint_id)
        return EndpointSpec.from_dict(row.data) if row else None

    def close(self) -> None:
        self.store.close()


def _endpoint_row(spec: EndpointSpec) -> EndpointRow:
    return EndpointRow(
        id=spec.id,
        http_method=spec.http_method,
        path=spec.path,
        framework=spec.framework,
        handler=spec.handler_qname,
        type_qname=spec.type_qname,
        file_path=spec.file_path,
        data=spec.to_dict(),
    )
