import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from apidocgen.cli import main
from apidocgen.ui.server import Workspace, make_handler


def test_cli_init_and_scan(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    assert (tmp_path / "apidocgen.yaml").exists()
    # point the template at an empty source dir and scan
    (tmp_path / "src/main/java").mkdir(parents=True)
    (tmp_path / "src/main/java/A.java").write_text("package a; public class A {}")
    assert main(["-c", str(tmp_path / "apidocgen.yaml"), "scan"]) == 0
    assert main(["-c", str(tmp_path / "apidocgen.yaml"), "endpoints"]) == 0
    out = capsys.readouterr().out
    assert "no endpoints detected" in out


def test_cli_full_flow_on_sample(sample_project, capsys):
    cfg = str(sample_project.cfg.path)
    assert main(["-c", cfg, "analyze", "--dry-run"]) == 0
    assert main(["-c", cfg, "analyze"]) == 0
    assert main(["-c", cfg, "render", "--no-archive"]) == 0
    assert main(["-c", cfg, "status"]) == 0
    assert main(["-c", cfg, "graph", "stats"]) == 0
    assert main(["-c", cfg, "cache", "stats"]) == 0
    out = capsys.readouterr().out
    assert "cache_misses=25" in out and "9 LLM calls" in out


def test_ui_server_roundtrip(sample_project):
    ws = Workspace.load(str(sample_project.cfg.path), None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ws))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        ws_info = json.loads(urllib.request.urlopen(base + "/api/workspace").read())
        assert ws_info["apps"][0]["endpoints"] == 8
        html = urllib.request.urlopen(base + "/").read().decode()
        assert "apidocgen" in html and "#2C673E" in html and "#E28B3C" in html and 'data-theme' in html
        assert "پروژه‌ها" in html and "/api/pick-folder" in html
        assert "تولید مستند" in html and "Finder" not in html
        assert "جستجوی سرویس" in html and "select2.min.js" in html
        slug = ws_info["apps"][0]["slug"]
        assert slug and slug != "0"
        from urllib.parse import quote
        by_name = json.loads(urllib.request.urlopen(base + "/api/apps/" + quote(slug) + "/endpoints").read())
        assert len(by_name["list"]) == 8
        eps = json.loads(urllib.request.urlopen(base + "/api/apps/0/endpoints").read())
        assert len(eps["list"]) == 8
        detail = json.loads(urllib.request.urlopen(base + "/api/apps/0/endpoint/POST%20%2FAPI%2FIssueDocument").read())
        assert detail["request"][0]["name"] == "TransactionId"
        req = urllib.request.Request(base + "/api/apps/0/jobs", data=json.dumps({"action": "run", "options": {"label": "t"}}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        job = json.loads(urllib.request.urlopen(req).read())
        for _ in range(100):
            j = json.loads(urllib.request.urlopen(f"{base}/api/apps/0/jobs/{job['id']}").read())
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.2)
        assert j["status"] == "done", j.get("error")
        arch = json.loads(urllib.request.urlopen(base + "/api/apps/0/archive").read())
        assert len(arch["entries"]) == 1 and arch["entries"][0]["label"] == "t"
        doc = urllib.request.urlopen(f"{base}/archive/0/{arch['entries'][0]['file']}").read().decode()
        assert "سرویس" in doc
        one = eps["list"][0]["id"]
        req = urllib.request.Request(
            base + "/api/apps/" + quote(slug) + "/jobs",
            data=json.dumps({"action": "render", "options": {"only": [one], "label": "one"}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        job = json.loads(urllib.request.urlopen(req).read())
        for _ in range(100):
            j = json.loads(urllib.request.urlopen(f"{base}/api/apps/{quote(slug)}/jobs/{job['id']}").read())
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.2)
        assert j["status"] == "done", j.get("error")
        assert j["result"]["render"]["endpoints"] == 1
        arch = json.loads(urllib.request.urlopen(base + "/api/apps/" + quote(slug) + "/archive").read())
        assert arch["entries"][0]["label"] == "one"
        named_doc = urllib.request.urlopen(
            f"{base}/archive/{quote(slug)}/{arch['entries'][0]['file']}"
        ).read().decode()
        assert "سرویس" in named_doc
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_app_registry_persists_and_dedupes(tmp_path):
    from apidocgen.ui.registry import AppRegistry

    root = tmp_path / "orders"
    (root / "src/main/java").mkdir(parents=True)
    (root / "src/main/java/A.java").write_text("package a; public class A {}")
    store = tmp_path / "apps.json"
    reg = AppRegistry(store)
    first = reg.add_root(str(root))
    assert (root / "apidocgen.yaml").exists()
    assert first["name"] == "orders"
    assert len(reg.entries) == 1
    again = reg.add_root(str(root))
    assert again["config"] == first["config"]
    assert len(reg.entries) == 1
    restored = AppRegistry(store)
    assert len(restored.entries) == 1 and restored.entries[0]["root"] == str(root.resolve())
    assert restored.remove_at(0) is True
    assert restored.entries == []


def test_ui_add_and_remove_project(tmp_path):
    proj = tmp_path / "wallet"
    (proj / "src/main/java").mkdir(parents=True)
    (proj / "src/main/java/A.java").write_text("package a; public class A {}")
    ws = Workspace.load(None, None, registry_path=tmp_path / "apps.json")
    assert ws.apps == []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ws))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        req = urllib.request.Request(
            base + "/api/apps",
            data=json.dumps({"path": str(proj)}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        added = json.loads(urllib.request.urlopen(req).read())
        assert added["name"] == "wallet" and added["slug"] == "wallet" and added["id"] == 0
        listed = json.loads(urllib.request.urlopen(base + "/api/workspace").read())
        assert len(listed["apps"]) == 1
        dup = json.loads(urllib.request.urlopen(req).read())
        assert dup["id"] == 0
        listed = json.loads(urllib.request.urlopen(base + "/api/workspace").read())
        assert len(listed["apps"]) == 1
        urllib.request.urlopen(urllib.request.Request(base + "/api/apps/0", method="DELETE"))
        listed = json.loads(urllib.request.urlopen(base + "/api/workspace").read())
        assert listed["apps"] == []
        saved = json.loads((tmp_path / "apps.json").read_text(encoding="utf-8"))
        assert saved["applications"] == []
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_slugify_uses_project_name():
    from apidocgen.ui.registry import slugify, unique_slug

    assert slugify("codingrelay") == "codingrelay"
    assert slugify("سامانه بانکداری باز") == "سامانه-بانکداری-باز"
    taken = set()
    assert unique_slug("orders", taken) == "orders"
    assert unique_slug("orders", taken) == "orders-2"
