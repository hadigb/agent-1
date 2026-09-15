"""Archive of generated documents (one entry per render)."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .docmodel.model import EndpointDoc
from .util.jalali import today_jalali


class Archive:
    def __init__(self, cfg: Config) -> None:
        root = cfg.resolve_path(cfg.get("project", "archive_dir")) if cfg.get("project", "archive_dir") else None
        self.root: Path = root or (cfg.db_path.parent / "archive")
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.cfg = cfg

    def entries(self) -> List[Dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return sorted(data.get("entries", []), key=lambda e: e.get("created_at", 0), reverse=True)

    def _save(self, entries: List[Dict[str, Any]]) -> None:
        self.index_path.write_text(json.dumps({"entries": entries}, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, html_path: Path, docs: List[EndpointDoc], meta: Dict[str, Any],
            usage: Optional[Dict[str, Any]] = None, label: str = "") -> Dict[str, Any]:
        ts = time.strftime("%Y%m%d-%H%M%S")
        version = str(meta.get("version", "") or "").replace("/", "_")
        stem = f"{ts}_v{version}" if version else ts
        dest = self.root / f"{stem}.html"
        shutil.copyfile(html_path, dest)
        content_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
        entries = self.entries()
        prev = entries[0] if entries else None
        prev_eps = {e["id"]: e for e in (prev.get("endpoints", []) if prev else [])}
        ep_entries = []
        for d in docs:
            h = hashlib.sha256(d.doc_hash_source().encode("utf-8")).hexdigest()
            ep_entries.append({"id": d.spec.id, "title": d.title, "method": d.http_method, "path": d.spec.path,
                               "anchor": d.anchor, "hash": h, "status": d.analysis_status,
                               "framework": d.spec.framework})
        added = [e["id"] for e in ep_entries if e["id"] not in prev_eps]
        changed = [e["id"] for e in ep_entries if e["id"] in prev_eps and prev_eps[e["id"]]["hash"] != e["hash"]]
        removed = [eid for eid in prev_eps if eid not in {e["id"] for e in ep_entries}]
        entry = {
            "file": dest.name, "created_at": time.time(), "created_jalali": today_jalali(),
            "created_iso": time.strftime("%Y-%m-%d %H:%M:%S"), "version": meta.get("version", ""),
            "title": meta.get("title", ""), "label": label, "size": dest.stat().st_size, "sha256": content_hash,
            "endpoint_count": len(docs), "endpoints": ep_entries, "added": added, "changed": changed, "removed": removed,
            "usage": usage or {},
        }
        entries.insert(0, entry)
        self._save(entries)
        (self.root / f"{stem}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
        return entry

    def path_for(self, name: str) -> Optional[Path]:
        p = (self.root / name).resolve()
        if p.parent != self.root.resolve() or not p.exists():
            return None
        return p

    def delete(self, name: str) -> bool:
        p = self.path_for(name)
        if not p:
            return False
        p.unlink()
        j = p.with_suffix(".json")
        if j.exists():
            j.unlink()
        self._save([e for e in self.entries() if e["file"] != name])
        return True
