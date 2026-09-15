"""File system scanner with content hashing and incremental parsing."""
from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from .config import Config
from .graph.store import GraphStore
from .javaparse import parse_java


@dataclass
class ScanResult:
    added: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    unchanged: int = 0
    parse_errors: Dict[str, str] = field(default_factory=dict)

    @property
    def touched(self) -> List[str]:
        return self.added + self.changed


def _match_any(rel: str, patterns: Iterable[str]) -> bool:
    rel_posix = rel.replace(os.sep, "/")
    for pat in patterns:
        if fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch("/" + rel_posix, pat):
            return True
        # allow "**/x/**" to also match a path that starts with "x/"
        if pat.startswith("**/") and fnmatch.fnmatch(rel_posix, pat[3:]):
            return True
    return False


def iter_source_files(cfg: Config) -> Iterable[Path]:
    include = cfg.get("project", "include", default=["**/*.java"]) or ["**/*.java"]
    exclude = cfg.get("project", "exclude", default=[]) or []
    follow = bool(cfg.get("project", "follow_symlinks", default=False))
    seen: Set[Path] = set()
    for root in cfg.scan_paths:
        if root is None:
            continue
        if root.is_file():
            if root not in seen:
                seen.add(root)
                yield root
            continue
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow):
            rel_dir = os.path.relpath(dirpath, root)
            rel_dir = "" if rel_dir == "." else rel_dir
            # prune excluded directories early
            keep: List[str] = []
            for d in dirnames:
                rel = os.path.join(rel_dir, d) if rel_dir else d
                if _match_any(rel + "/", exclude) or _match_any(rel + "/x", exclude) or d in (".git", "node_modules"):
                    continue
                keep.append(d)
            dirnames[:] = keep
            for fn in filenames:
                rel = os.path.join(rel_dir, fn) if rel_dir else fn
                if not _match_any(rel, include):
                    continue
                if _match_any(rel, exclude):
                    continue
                p = Path(dirpath) / fn
                if p not in seen:
                    seen.add(p)
                    yield p


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_text(p: Path) -> str:
    data = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def scan_files(cfg: Config, store: GraphStore, progress: Optional[Callable[[str], None]] = None,
               force: bool = False) -> ScanResult:
    """Hash every source file; parse only new/changed ones; drop vanished ones."""
    result = ScanResult()
    known = store.file_hashes()
    present: Set[str] = set()
    missing_roots = [str(r) for r in cfg.scan_paths if r is not None and not r.exists()]
    for r in missing_roots:
        result.parse_errors[r] = "scan path not found - previously scanned files under it were kept"
    for p in iter_source_files(cfg):
        path = str(p)
        present.add(path)
        try:
            data = p.read_bytes()
        except OSError as e:
            result.parse_errors[path] = f"unreadable: {e}"
            continue
        sha = sha256_bytes(data)
        if not force and known.get(path) == sha:
            result.unchanged += 1
            continue
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = read_text(p)
        jf = parse_java(text, path)
        err = "; ".join(jf.errors) if jf.errors else None
        if err:
            result.parse_errors[path] = err
        st = p.stat()
        store.upsert_file(path, sha, st.st_size, st.st_mtime, jf, err)
        if path in known:
            result.changed.append(path)
        else:
            result.added.append(path)
        if progress:
            progress(path)
    removed = [p for p in known if p not in present
               and not any(p.startswith(r.rstrip("/\\") + os.sep) or p == r for r in missing_roots)]
    if removed:
        store.delete_files(removed)
        result.removed = removed
    store.conn.commit()
    return result
