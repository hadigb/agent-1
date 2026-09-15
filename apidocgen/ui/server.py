"""Minimal local web UI (standard library only).

* home screen: persistent list of Java projects (native folder picker to add)
* per-project page: scan, analysis, and document generation
* endpoint tree, archive of generated documents, background jobs
"""
from __future__ import annotations

import contextlib
import json
import mimetypes
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..archive import Archive
from ..config import Config
from ..pipeline import (
    do_analyze, do_render, do_scan, do_ucs, endpoint_doc_summary, endpoint_tree, make_llm, status_info,
)
from ..project import Project
from .folder_picker import pick_folder
from .registry import AppRegistry, default_registry_path, slugify, unique_slug

STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, idx: int, name: str, config_path: Path, root: str = "", slug: str = "") -> None:
        self.idx = idx
        self.name = name
        self.slug = slug or slugify(name)
        self.config_path = config_path
        self.root = root or str(config_path.parent)
        self.lock = threading.Lock()          # one job at a time per app
        self.jobs: List[Dict[str, Any]] = []
        self._index_cache: Optional[tuple] = None   # (last_scan_at, CodeIndex) shared between requests
        self._cache_lock = threading.Lock()

    def project(self) -> Project:
        p = Project(Config.load(str(self.config_path)))
        with self._cache_lock:
            if self._index_cache and self._index_cache[0] == p.store.get_meta("last_scan_at"):
                p._index = self._index_cache[1]
                p._index_stamp = self._index_cache[0]
        return p

    def release(self, p: Project) -> None:
        """Remember the (read-only) code index so the next request does not rebuild it, then close.

        The index is stored together with the scan stamp that was current *when it was built*, so an index built
        while a scan job was running is never mistaken for the post-scan one.
        """
        try:
            if p._index is not None and p._index_stamp is not None:
                with self._cache_lock:
                    if not self._index_cache or self._index_cache[0] != p._index_stamp:
                        self._index_cache = (p._index_stamp, p._index)
        finally:
            p.close()

    @contextlib.contextmanager
    def session(self):
        p = self.project()
        try:
            yield p
        finally:
            self.release(p)

    def summary(self) -> Dict[str, Any]:
        try:
            with self.session() as p:
                stats = p.store.graph_stats()
                usage = p.store.usage_totals()
                cache = p.store.cache_stats()
                last = p.store.get_meta("last_scan_at")
                arch = Archive(p.cfg).entries()
                return {"id": self.idx, "slug": self.slug, "name": self.name, "config": str(self.config_path),
                        "root": self.root, "ok": True,
                        "project_name": p.cfg.get("project", "name"), "paths": [str(x) for x in p.cfg.scan_paths],
                        "endpoints": stats["endpoints"], "files": stats["files"], "symbols": stats["symbols"],
                        "last_scan_at": float(last) if last else None, "usage": usage, "cache": cache,
                        "documents": len(arch), "latest_document": arch[0]["file"] if arch else None,
                        "llm": {"provider": p.cfg.get("llm", "provider"), "model": p.cfg.get("llm", "model")},
                        "busy": self.lock.locked()}
        except Exception as e:  # pragma: no cover
            return {"id": self.idx, "slug": self.slug, "name": self.name, "config": str(self.config_path),
                    "root": self.root, "ok": False, "error": str(e)}

    # ------------------------------------------------------------------ jobs
    def start_job(self, action: str, options: Dict[str, Any]) -> Dict[str, Any]:
        job = {"id": len(self.jobs) + 1, "action": action, "status": "queued", "log": [], "started": time.time(),
               "finished": None, "result": None, "error": None}
        self.jobs.append(job)
        t = threading.Thread(target=self._run_job, args=(job, options), daemon=True)
        t.start()
        return job

    def _run_job(self, job: Dict[str, Any], options: Dict[str, Any]) -> None:
        def log(msg: str) -> None:
            job["log"].append(msg)

        if not self.lock.acquire(timeout=0.1):
            job["status"] = "failed"
            job["error"] = "another job is running for this application"
            job["finished"] = time.time()
            return
        job["status"] = "running"
        try:
            p = self.project()
            try:
                action = job["action"]
                result: Dict[str, Any] = {}
                if action in ("scan", "run"):
                    result["scan"] = do_scan(p, log, force=bool(options.get("force")))
                if action in ("analyze", "run"):
                    dry = bool(options.get("dry_run"))
                    client = None if dry else make_llm(p.cfg, options.get("provider"))
                    if options.get("model") and client is not None:
                        client.model = options["model"]
                    result["analyze"] = do_analyze(p, client, log, dry_run=dry, only=options.get("only"))
                if action in ("render", "run"):
                    result["render"] = do_render(p, log, only=options.get("only"), label=options.get("label", ""))
                if action == "ucs":
                    dry = bool(options.get("dry_run"))
                    client = None if dry else make_llm(p.cfg, options.get("provider"))
                    if options.get("model") and client is not None:
                        client.model = options["model"]
                    result["ucs"] = do_ucs(
                        p, client, log, mode=options.get("mode") or "new",
                        endpoint_id=options.get("endpoint_id"),
                        previous_analysis=options.get("previous_analysis") or "",
                        new_requirement=options.get("new_requirement") or "",
                        dry_run=dry,
                    )
                job["result"] = result
                job["status"] = "done"
            finally:
                self.release(p)
        except Exception as e:
            job["status"] = "failed"
            job["error"] = f"{e}\n{traceback.format_exc()}"
            log(f"! {e}")
        finally:
            job["finished"] = time.time()
            self.lock.release()


class Workspace:
    def __init__(self, registry: AppRegistry) -> None:
        self.registry = registry
        self.apps: List[App] = []
        self.lock = threading.Lock()
        self._pick_lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        old = {str(a.config_path.resolve()): a for a in self.apps}
        apps: List[App] = []
        for i, e in enumerate(self.registry.entries):
            cp = Path(e["config"]).resolve()
            prev = old.get(str(cp))
            name = e.get("name") or cp.parent.name
            root = e.get("root") or str(cp.parent)
            if prev:
                prev.idx = i
                prev.name = name
                prev.root = root
                apps.append(prev)
            else:
                apps.append(App(i, name, cp, root=root))
        taken: set = set()
        for a in apps:
            a.slug = unique_slug(a.name, taken)
        self.apps = apps

    @staticmethod
    def load(config: Optional[str], workspace: Optional[str],
             registry_path: Optional[Path] = None) -> "Workspace":
        if workspace:
            wp = Path(workspace).resolve()
            data = yaml.safe_load(wp.read_text(encoding="utf-8")) or {}
            reg = AppRegistry(None)
            for a in data.get("applications", []) or []:
                cp = Path(a["config"])
                if not cp.is_absolute():
                    cp = (wp.parent / cp).resolve()
                if cp.exists():
                    reg.add_config(str(cp), name=a.get("name") or "", root=str(cp.parent))
            return Workspace(reg)
        if config is not None:
            # Explicit config (tests / ``apidocgen -c … ui``): in-memory, no ~/.apidocgen write.
            reg = AppRegistry(None)
            cp = Path(config).expanduser()
            if cp.exists():
                reg.add_config(str(cp))
            else:
                reg.entries.append({"name": cp.parent.name, "config": str(cp.resolve()), "root": str(cp.parent)})
            return Workspace(reg)
        path = registry_path if registry_path is not None else default_registry_path()
        reg = AppRegistry(path)
        if not reg.entries and registry_path is None:
            cwd_cfg = Path("apidocgen.yaml")
            if cwd_cfg.exists():
                reg.seed_from_config(str(cwd_cfg))
        return Workspace(reg)

    def app(self, key) -> Optional[App]:
        token = str(key)
        for a in self.apps:
            if a.slug == token or a.name == token:
                return a
        try:
            idx = int(token)
        except (TypeError, ValueError):
            return None
        return self.apps[idx] if 0 <= idx < len(self.apps) else None

    def add_root(self, root: str) -> App:
        with self.lock:
            self.registry.add_root(root)
            self.reload()
            folder = str(Path(root).expanduser().resolve())
            for a in self.apps:
                if a.root == folder or str(a.config_path.parent.resolve()) == folder:
                    return a
            return self.apps[-1]

    def remove_key(self, key) -> bool:
        app = self.app(key)
        if not app:
            return False
        return self.remove_at(app.idx)

    def remove_at(self, idx: int) -> bool:
        with self.lock:
            ok = self.registry.remove_at(idx)
            if ok:
                self.reload()
            return ok


def make_handler(ws: Workspace):
    class Handler(BaseHTTPRequestHandler):
        server_version = "apidocgen-ui/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
            if self.path.startswith("/api/") and "jobs" in self.path:
                return
            super().log_message(fmt, *args)

        # -------------------------------------------------------------- helpers
        def _json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path, content_type: Optional[str] = None) -> None:
            if not path.exists():
                self._json({"error": "not found"}, 404)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type or (mimetypes.guess_type(str(path))[0] or "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> Dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            if n == 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        # -------------------------------------------------------------- routing
        def do_GET(self) -> None:
            try:
                self._route("GET")
            except BrokenPipeError:
                pass
            except Exception as e:  # pragma: no cover
                self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

        def do_POST(self) -> None:
            try:
                self._route("POST")
            except Exception as e:  # pragma: no cover
                self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

        def do_DELETE(self) -> None:
            try:
                self._route("DELETE")
            except Exception as e:  # pragma: no cover
                self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

        def _route(self, method: str) -> None:
            parsed = urllib.parse.urlparse(self.path)
            parts = [urllib.parse.unquote(x) for x in parsed.path.split("/") if x]
            qs = urllib.parse.parse_qs(parsed.query)
            if method == "GET" and not parts:
                return self._file(STATIC / "index.html", "text/html; charset=utf-8")
            if method == "GET" and parts[0] == "static" and len(parts) == 2:
                name = parts[1]
                target = (STATIC / name).resolve()
                if any(x in name for x in ("/", "\\", "..")) or target.parent != STATIC.resolve():
                    return self._json({"error": "not found"}, 404)
                return self._file(target)
            if parts[0] == "archive" and len(parts) == 3 and method == "GET":
                app = ws.app(parts[1])
                if not app:
                    return self._json({"error": "no such app"}, 404)
                with app.session() as p:
                    f = Archive(p.cfg).path_for(parts[2])
                if not f:
                    return self._json({"error": "not found"}, 404)
                return self._file(f, "text/html; charset=utf-8")
            if parts[0] != "api":
                return self._json({"error": "not found"}, 404)
            if len(parts) == 2 and parts[1] == "workspace":
                return self._json({"apps": [a.summary() for a in ws.apps]})
            if method == "POST" and parts == ["api", "pick-folder"]:
                if not ws._pick_lock.acquire(blocking=False):
                    return self._json({"error": "folder picker already open"}, 409)
                try:
                    path = pick_folder()
                finally:
                    ws._pick_lock.release()
                if not path:
                    return self._json({"cancelled": True, "path": None})
                return self._json({"cancelled": False, "path": path})
            if method == "POST" and parts == ["api", "apps"]:
                body = self._body()
                folder = (body.get("path") or body.get("root") or "").strip()
                if not folder:
                    return self._json({"error": "path is required"}, 400)
                try:
                    app = ws.add_root(folder)
                except FileNotFoundError as e:
                    return self._json({"error": str(e)}, 404)
                except Exception as e:
                    return self._json({"error": str(e)}, 400)
                return self._json(app.summary())
            if len(parts) >= 3 and parts[1] == "apps":
                key = parts[2]
                if method == "DELETE" and len(parts) == 3:
                    if not ws.remove_key(key):
                        return self._json({"error": "no such app"}, 404)
                    return self._json({"ok": True, "apps": [a.summary() for a in ws.apps]})
                app = ws.app(key)
                if not app:
                    return self._json({"error": "no such app"}, 404)
                rest = parts[3:]
                return self._app_route(method, app, rest, qs)
            self._json({"error": "not found"}, 404)

        def _app_route(self, method: str, app: App, rest: List[str], qs: Dict[str, List[str]]) -> None:
            if not rest and method == "GET":
                return self._json(app.summary())
            if rest == ["status"] and method == "GET":
                with app.session() as p:
                    return self._json(status_info(p))
            if rest == ["endpoints"] and method == "GET":
                with app.session() as p:
                    specs = p.endpoints()
                    latest = Archive(p.cfg).entries()
                    anchors = {e["id"]: e["anchor"] for e in (latest[0]["endpoints"] if latest else [])}
                    return self._json({"tree": endpoint_tree(specs), "latest_document": latest[0]["file"] if latest else None,
                                       "anchors": anchors,
                                       "list": [{"id": s.id, "method": s.http_method, "path": s.path, "framework": s.framework,
                                                 "handler": s.handler_qname, "file": s.file_path, "summary": s.summary}
                                                for s in specs]})
            if len(rest) == 2 and rest[0] == "endpoint" and method == "GET":
                with app.session() as p:
                    spec = p.endpoint(rest[1])
                    if spec is None:
                        return self._json({"error": "no such endpoint"}, 404)
                    from ..analysis.analyzer import Analyzer

                    an = Analyzer(p, None)
                    doc = an.build_doc(spec, 0)
                    data = endpoint_doc_summary(doc)
                    data["spec"] = spec.to_dict()
                    # cache status of the units
                    base = an.doc_builder.build(spec, 0)
                    _t, _e, status = an.results_for(spec, base)
                    data["analysis_status"] = status
                    return self._json(data)
            if rest == ["graph"] and method == "GET":
                with app.session() as p:
                    raw = p.store.get_meta("controller_graph") or "[]"
                    return self._json({"controllers": json.loads(raw), "endpoints": [
                        {"id": s.id, "method": s.http_method, "path": s.path, "handler": s.handler_qname}
                        for s in p.endpoints()]})
            if rest == ["ucs"] and method == "GET":
                with app.session() as p:
                    return self._json({"entries": p.store.list_ucs()})
            if len(rest) == 2 and rest[0] == "ucs" and method == "GET":
                with app.session() as p:
                    doc = p.store.get_ucs(int(rest[1]))
                    if not doc:
                        return self._json({"error": "no such document"}, 404)
                    return self._json(doc)
            if rest == ["archive"] and method == "GET":
                with app.session() as p:
                    return self._json({"entries": Archive(p.cfg).entries()})
            if len(rest) == 2 and rest[0] == "archive" and method == "DELETE":
                with app.session() as p:
                    ok = Archive(p.cfg).delete(rest[1])
                    return self._json({"ok": ok})
            if rest == ["jobs"] and method == "GET":
                return self._json({"jobs": [self._job_view(j, full=False) for j in app.jobs[-20:]]})
            if rest == ["jobs"] and method == "POST":
                body = self._body()
                action = body.get("action")
                if action not in ("scan", "analyze", "render", "run", "ucs"):
                    return self._json({"error": "unknown action"}, 400)
                job = app.start_job(action, body.get("options") or {})
                return self._json(self._job_view(job, full=True))
            if len(rest) == 2 and rest[0] == "jobs" and method == "GET":
                jid = int(rest[1])
                for j in app.jobs:
                    if j["id"] == jid:
                        return self._json(self._job_view(j, full=True))
                return self._json({"error": "no such job"}, 404)
            self._json({"error": "not found"}, 404)

        @staticmethod
        def _job_view(j: Dict[str, Any], full: bool) -> Dict[str, Any]:
            v = {k: j[k] for k in ("id", "action", "status", "started", "finished", "error")}
            v["log"] = j["log"] if full else j["log"][-3:]
            v["result"] = j["result"] if full else None
            return v

    return Handler


def serve(config: Optional[str], workspace: Optional[str], host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = True, registry_path: Optional[Path] = None) -> int:
    ws = Workspace.load(config, workspace, registry_path=registry_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(ws))
    url = f"http://{host}:{port}/"
    print(f"apidocgen UI: {url}  ({len(ws.apps)} application(s))  - press Ctrl+C to stop")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
