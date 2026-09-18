"""Persistent list of Java applications shown on the home screen.

The default store is a SQLite catalog (``~/.apidocgen/apps.db``). Each project
gets its own graph database under ``~/.apidocgen/projects/<slug>/graph.db``.
A ``.json`` path is still accepted so existing tests keep writing a simple file.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..config import CONFIG_FILENAME, Config, deep_merge


def slugify(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"[\s/_]+", "-", s)
    s = re.sub(r"[^\w\u0600-\u06FF.-]+", "", s, flags=re.UNICODE)
    return s.strip(".-") or "project"


def unique_slug(name: str, taken: set) -> str:
    base = slugify(name)
    slug = base
    n = 2
    while slug.casefold() in taken:
        slug = f"{base}-{n}"
        n += 1
    taken.add(slug.casefold())
    return slug


def default_registry_path() -> Path:
    return Path.home() / ".apidocgen" / "apps.db"


def source_paths_for(root: Path) -> List[str]:
    java = root / "src" / "main" / "java"
    if java.is_dir():
        return ["./src/main/java"]
    return ["."]


def isolated_db_path(registry_path: Optional[Path], slug: str) -> Path:
    root = (registry_path.parent if registry_path else Path.home() / ".apidocgen")
    return root / "projects" / slug / "graph.db"


def ensure_project_config(root: Path, overrides: Optional[Dict[str, Any]] = None) -> Path:
    """Use an existing apidocgen.yaml or write one for the chosen folder."""
    root = root.resolve()
    cfg = root / CONFIG_FILENAME
    data: Dict[str, Any] = {}
    if cfg.exists():
        loaded = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    name = (overrides or {}).get("project", {}).get("name") if overrides else None
    name = name or data.get("project", {}).get("name") or root.name
    base = {
        "project": {
            "name": name,
            "paths": source_paths_for(root),
            "db": data.get("project", {}).get("db") or ".apidocgen/graph.db",
        },
        "doc": {
            "title": f"مستندات API — {name}",
            "system_name": name,
            "output": f"docs/{name}-api.html",
        },
    }
    merged = deep_merge(base, data)
    if overrides:
        merged = deep_merge(merged, overrides)
    cfg.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return cfg


def entry_name(config_path: Path, fallback: str = "") -> str:
    try:
        return Config.load(str(config_path)).get("project", "name") or fallback or config_path.parent.name
    except Exception:
        return fallback or config_path.parent.name


_SQLITE = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    name TEXT NOT NULL,
    root TEXT NOT NULL,
    config_path TEXT,
    db_path TEXT,
    config_json TEXT,
    created_at REAL NOT NULL
);
"""


class AppRegistry:
    """Catalog of projects. ``path=None`` keeps the list in memory (tests)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self.entries: List[Dict[str, str]] = []
        self._backend = "json" if path is not None and str(path).endswith(".json") else "sqlite"
        self.load()

    def load(self) -> None:
        if self.path is None:
            self.entries = []
            return
        if self._backend == "json":
            self._load_json()
            return
        self._load_sqlite()
        self._import_legacy_json()

    def _load_json(self) -> None:
        if not self.path.exists():
            self.entries = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.entries = []
            return
        self.entries = list(data.get("applications") or [])

    def _connect(self) -> sqlite3.Connection:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.executescript(_SQLITE)
        conn.commit()
        return conn

    def _load_sqlite(self) -> None:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT slug, name, root, config_path, db_path, config_json FROM projects ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        self.entries = [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> Dict[str, str]:
        return {
            "name": row["name"],
            "config": row["config_path"] or "",
            "root": row["root"],
            "slug": row["slug"] or "",
            "db": row["db_path"] or "",
            "config_json": row["config_json"] or "",
        }

    def _import_legacy_json(self) -> None:
        if self.path is None or self.entries:
            return
        legacy = self.path.with_suffix(".json")
        if not legacy.exists():
            home_json = Path.home() / ".apidocgen" / "apps.json"
            if self.path.resolve() == default_registry_path().resolve() and home_json.exists():
                legacy = home_json
            else:
                return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in data.get("applications") or []:
            config = item.get("config") or ""
            if config:
                try:
                    self.add_config(config, name=item.get("name") or "", root=item.get("root") or "")
                except FileNotFoundError:
                    continue
        self._load_sqlite()

    def save(self) -> None:
        if self.path is None:
            return
        if self._backend == "json":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"applications": self.entries}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return
        conn = self._connect()
        try:
            conn.execute("DELETE FROM projects")
            for entry in self.entries:
                conn.execute(
                    "INSERT INTO projects(slug, name, root, config_path, db_path, config_json, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (
                        entry.get("slug") or slugify(entry.get("name") or "project"),
                        entry.get("name") or "",
                        entry.get("root") or "",
                        entry.get("config") or "",
                        entry.get("db") or "",
                        entry.get("config_json") or "",
                        time.time(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def add_root(self, root: str, doc: Optional[Dict[str, Any]] = None,
                 project: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        folder = Path(root).expanduser().resolve()
        if not folder.is_dir():
            raise FileNotFoundError(f"folder not found: {folder}")
        taken = {slugify(e.get("name") or "") for e in self.entries}
        taken |= {e.get("slug") or "" for e in self.entries}
        name = (project or {}).get("name") or folder.name
        slug = unique_slug(name, taken)
        overrides: Dict[str, Any] = {"project": {"name": name, "paths": source_paths_for(folder)}}
        if self._backend == "sqlite" and self.path is not None:
            db_path = isolated_db_path(self.path, slug)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            overrides["project"]["db"] = str(db_path)
        if project:
            overrides["project"] = deep_merge(overrides["project"], project)
        if doc:
            overrides["doc"] = doc
        if (project or {}).get("failure_type") or (doc or {}).get("failure_type"):
            overrides.setdefault("doc", {})
            overrides["doc"]["failure_type"] = (doc or {}).get("failure_type") or (project or {}).get("failure_type")
        cfg = ensure_project_config(folder, overrides)
        return self.add_config(str(cfg), name=name, root=str(folder), slug=slug)

    def add_config(self, config: str, name: str = "", root: str = "", slug: str = "") -> Dict[str, str]:
        cfg = Path(config).expanduser().resolve()
        if not cfg.exists():
            raise FileNotFoundError(f"config not found: {cfg}")
        for existing in self.entries:
            if existing.get("config") and Path(existing["config"]).resolve() == cfg:
                return existing
        loaded = Config.load(str(cfg))
        db = str(loaded.db_path)
        entry = {
            "name": loaded.get("project", "name") or name or cfg.parent.name,
            "config": str(cfg),
            "root": root or str(cfg.parent),
            "slug": slug or slugify(name or cfg.parent.name),
            "db": db,
            "config_json": json.dumps(loaded.data, ensure_ascii=False),
        }
        self.entries.append(entry)
        self.save()
        return entry

    def remove_at(self, index: int) -> bool:
        if index < 0 or index >= len(self.entries):
            return False
        self.entries.pop(index)
        self.save()
        return True

    def seed_from_config(self, config: str) -> None:
        if self.entries:
            return
        p = Path(config).expanduser()
        if p.exists():
            self.add_config(str(p))
