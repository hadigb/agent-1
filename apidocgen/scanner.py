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


def _match_any(relative: str, patterns: Iterable[str]) -> bool:
    posix_path = relative.replace(os.sep, "/")
    for pattern in patterns:
        if fnmatch.fnmatch(posix_path, pattern) or fnmatch.fnmatch("/" + posix_path, pattern):
            return True
        # allow "**/x/**" to also match a path that starts with "x/"
        if pattern.startswith("**/") and fnmatch.fnmatch(posix_path, pattern[3:]):
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
            relative_dir = os.path.relpath(dirpath, root)
            relative_dir = "" if relative_dir == "." else relative_dir
            kept_dirs: List[str] = []
            for dirname in dirnames:
                relative = os.path.join(relative_dir, dirname) if relative_dir else dirname
                skip_dir = (
                    _match_any(relative + "/", exclude)
                    or _match_any(relative + "/x", exclude)
                    or dirname in (".git", "node_modules")
                )
                if not skip_dir:
                    kept_dirs.append(dirname)
            dirnames[:] = kept_dirs
            for filename in filenames:
                relative = os.path.join(relative_dir, filename) if relative_dir else filename
                if not _match_any(relative, include) or _match_any(relative, exclude):
                    continue
                path = Path(dirpath) / filename
                if path not in seen:
                    seen.add(path)
                    yield path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def scan_files(
    cfg: Config,
    store: GraphStore,
    progress: Optional[Callable[[str], None]] = None,
    force: bool = False,
) -> ScanResult:
    """Hash every source file; parse only new/changed ones; drop vanished ones."""
    result = ScanResult()
    known = store.file_hashes()
    missing_source = set(store.paths_missing_source()) if not force else set()
    present: Set[str] = set()
    missing_roots = [str(root) for root in cfg.scan_paths if root is not None and not root.exists()]
    for missing in missing_roots:
        result.parse_errors[missing] = (
            "scan path not found - previously scanned files under it were kept"
        )

    for path in iter_source_files(cfg):
        path_str = str(path)
        present.add(path_str)
        try:
            data = path.read_bytes()
        except OSError as error:
            result.parse_errors[path_str] = f"unreadable: {error}"
            continue
        digest = sha256_bytes(data)
        if not force and known.get(path_str) == digest:
            if path_str in missing_source:
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = read_text(path)
                store.update_file_source(path_str, text)
            result.unchanged += 1
            continue
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = read_text(path)
        java_file = parse_java(text, path_str)
        parse_error = "; ".join(java_file.errors) if java_file.errors else None
        if parse_error:
            result.parse_errors[path_str] = parse_error
        stat = path.stat()
        store.upsert_file(path_str, digest, stat.st_size, stat.st_mtime, java_file, parse_error, source=text)
        if path_str in known:
            result.changed.append(path_str)
        else:
            result.added.append(path_str)
        if progress:
            progress(path_str)

    removed = [
        known_path for known_path in known
        if known_path not in present
        and not any(
            known_path.startswith(root.rstrip("/\\") + os.sep) or known_path == root
            for root in missing_roots
        )
    ]
    if removed:
        store.delete_files(removed)
        result.removed = removed
    store.conn.commit()
    return result
