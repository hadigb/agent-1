"""Project facade: config + store + index + endpoints."""
from __future__ import annotations

import json
import time
from pathlib import Path
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
        self._index_stamp: Optional[str] = None   # last_scan_at value the index was built from

    # ------------------------------------------------------------------ index
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

    # ------------------------------------------------------------------ scanning
    def manual_entries(self) -> List[Dict[str, Any]]:
        p = self.cfg.resolve_path(self.cfg.get("project", "endpoints_file"))
        if not p or not p.exists():
            return []
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("endpoints", []) or []

    def scan(self, progress: Optional[Callable[[str], None]] = None, force: bool = False) -> ScanResult:
        t0 = time.time()
        result = scan_files(self.cfg, self.store, progress=progress, force=force)
        self.store.set_meta("last_scan_at", str(time.time()))
        self.invalidate_index()
        self.rebuild_graph()
        self.store.set_meta("last_scan_seconds", f"{time.time() - t0:.2f}")
        return result

    def rebuild_graph(self) -> List[EndpointSpec]:
        index = self.index
        symbols, edges = GraphBuilder(index).build()
        detectors = build_detectors(self.cfg.data, self.manual_entries())
        specs = detect_endpoints(index, detectors)
        rows = [EndpointRow(id=s.id, http_method=s.http_method, path=s.path, framework=s.framework,
                            handler=s.handler_qname, type_qname=s.type_qname, file_path=s.file_path, data=s.to_dict())
                for s in specs]
        self.store.replace_graph(symbols, edges, rows)
        return specs

    # ------------------------------------------------------------------ endpoints
    def endpoints(self) -> List[EndpointSpec]:
        return [EndpointSpec.from_dict(r.data) for r in self.store.list_endpoints()]

    def endpoint(self, eid: str) -> Optional[EndpointSpec]:
        r = self.store.get_endpoint(eid)
        return EndpointSpec.from_dict(r.data) if r else None

    def close(self) -> None:
        self.store.close()
