"""Persistent list of Java applications shown on the home screen."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..config import CONFIG_FILENAME, Config


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
    return Path.home() / ".apidocgen" / "apps.json"


def source_paths_for(root: Path) -> List[str]:
    java = root / "src" / "main" / "java"
    if java.is_dir():
        return ["./src/main/java"]
    return ["."]


def ensure_project_config(root: Path) -> Path:
    """Use an existing apidocgen.yaml or write a minimal one for the chosen folder."""
    root = root.resolve()
    cfg = root / CONFIG_FILENAME
    if cfg.exists():
        return cfg
    name = root.name
    data = {
        "project": {
            "name": name,
            "paths": source_paths_for(root),
            "db": ".apidocgen/graph.db",
        },
        "doc": {
            "title": f"مستندات API — {name}",
            "system_name": name,
            "output": f"docs/{name}-api.html",
        },
    }
    cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return cfg


def entry_name(config_path: Path, fallback: str = "") -> str:
    try:
        return Config.load(str(config_path)).get("project", "name") or fallback or config_path.parent.name
    except Exception:
        return fallback or config_path.parent.name


class AppRegistry:
    """JSON catalog of projects. ``path=None`` keeps the list in memory (tests)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self.entries: List[Dict[str, str]] = []
        self.load()

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            self.entries = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.entries = []
            return
        self.entries = list(data.get("applications") or [])

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"applications": self.entries}, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_root(self, root: str) -> Dict[str, str]:
        folder = Path(root).expanduser().resolve()
        if not folder.is_dir():
            raise FileNotFoundError(f"folder not found: {folder}")
        cfg = ensure_project_config(folder)
        return self.add_config(str(cfg), name=folder.name, root=str(folder))

    def add_config(self, config: str, name: str = "", root: str = "") -> Dict[str, str]:
        cfg = Path(config).expanduser().resolve()
        if not cfg.exists():
            raise FileNotFoundError(f"config not found: {cfg}")
        resolved = str(cfg)
        for existing in self.entries:
            if Path(existing["config"]).resolve() == cfg:
                return existing
        entry = {
            "name": entry_name(cfg, name),
            "config": resolved,
            "root": root or str(cfg.parent),
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
