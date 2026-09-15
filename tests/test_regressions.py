"""Regression tests for issues found in review."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from apidocgen.analysis import Analyzer
from apidocgen.config import Config
from apidocgen.docmodel import DocBuilder
from apidocgen.docmodel.fields import substitute
from apidocgen.javaparse.model import TypeRef
from apidocgen.llm import MockClient
from apidocgen.llm.base import LLMResponse
from apidocgen.pipeline import build_docs, do_render
from apidocgen.project import Project
from apidocgen.ui.server import Workspace, make_handler


def make_project(tmp_path, files: dict, extra_cfg: str = "") -> Project:
    src = tmp_path / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (tmp_path / "apidocgen.yaml").write_text(
        "project:\n  paths: [./src]\nllm:\n  provider: mock\n  model: mock-1\n" + extra_cfg, encoding="utf-8")
    p = Project(Config.load(str(tmp_path / "apidocgen.yaml")))
    p.scan()
    return p


def test_self_referential_generic_base_controller_does_not_recurse(tmp_path):
    p = make_project(tmp_path, {
        "a/ApiResponse.java": "package a; import java.util.List; public class ApiResponse<T> { public T data; public boolean ok; }",
        "a/CrudController.java": "package a; import java.util.List; import org.springframework.web.bind.annotation.*;\n"
                                 "public abstract class CrudController<T> { @GetMapping(\"/list\") public ApiResponse<List<T>> list() { return null; } }",
        "a/UserController.java": "package a; import org.springframework.web.bind.annotation.*;\n"
                                 "@RestController @RequestMapping(\"/users\") public class UserController extends CrudController<User> {}",
        "a/User.java": "package a; public class User { public String name; }",
    })
    an = Analyzer(p, MockClient())
    plan = an.plan()  # used to raise RecursionError
    assert plan.specs
    ids = {s.id for s in p.endpoints()}
    assert "GET /users/list" in ids
    assert "GET /list" not in ids
    assert substitute(TypeRef("T"), {"T": TypeRef("List", args=[TypeRef("T")])}).canonical() == "List<T>"


def test_budget_respected_when_batches_split(sample_project):
    class Bad(MockClient):
        def complete(self, system, user, json_mode=True):
            return LLMResponse(text="{ nope", input_tokens=10, output_tokens=1, stop_reason="stop")

    sample_project.cfg.data["llm"]["max_calls_per_run"] = 3
    an = Analyzer(sample_project, Bad())
    rep = an.run(an.plan())
    assert rep.calls <= 4 and "max_calls_per_run" in rep.stopped_reason


def test_truncated_batches_are_split_without_retry(sample_project):
    calls = []

    class Trunc(MockClient):
        def complete(self, system, user, json_mode=True):
            n = user.count('"kind": "type"')
            calls.append(n)
            if n > 3:
                return LLMResponse(text='{"results": {"x": {', input_tokens=5, output_tokens=5, stop_reason="length")
            return super().complete(system, user, json_mode)

    an = Analyzer(sample_project, Trunc())
    rep = an.run(an.plan())
    assert rep.failed_units == [] and rep.analysed_units == 25
    big = [n for n in calls if n > 3]
    assert len(big) == len(set(big)) or calls.count(17) == 1  # a truncated batch is never retried as-is


def test_unexpected_json_shapes_do_not_crash(sample_project):
    class Weird(MockClient):
        def complete(self, system, user, json_mode=True):
            return LLMResponse(text='[1, 2, 3]', input_tokens=1, output_tokens=1)

    an = Analyzer(sample_project, Weird())
    rep = an.run(an.plan())
    assert rep.failed_units and rep.calls > 0

    class Weird2(MockClient):
        def complete(self, system, user, json_mode=True):
            r = json.loads(super().complete(system, user, json_mode).text)
            for v in r["results"].values():
                v["fields"] = ["not", "a", "dict"]
                v["params"] = ["x"]
            return LLMResponse(text=json.dumps(r, ensure_ascii=False), input_tokens=1, output_tokens=1)

    rep2 = Analyzer(sample_project, Weird2()).run(Analyzer(sample_project, Weird2()).plan())
    assert rep2.calls > 0  # type units fail (fields is a list), endpoints survive with empty params


def test_cache_key_with_model_is_stable_across_render(sample_project, tmp_path):
    sample_project.cfg.data["analysis"]["cache_key_includes_model"] = True
    an = Analyzer(sample_project, MockClient())
    an.run(an.plan())
    _an, specs, docs, _a = build_docs(sample_project, None)
    assert all(d.analysis_status == "cached" for d in docs)
    assert Analyzer(sample_project, None).prune_cache(Analyzer(sample_project, None).plan()) == 0


def test_default_value_makes_primitive_optional_and_map_params_are_catch_all(tmp_path):
    p = make_project(tmp_path, {
        "a/C.java": "package a; import java.util.Map; import org.springframework.web.bind.annotation.*;\n"
                    "@RestController public class C { @GetMapping(\"/x\") public String x(@RequestParam(defaultValue=\"10\") int pageSize,"
                    " @RequestParam Map<String,String> all, @RequestHeader Map<String,String> hdrs) { return null; } }",
    })
    e = p.endpoints()[0]
    ps = {x.name: x for x in e.params}
    assert ps["pageSize"].required is False and ps["pageSize"].default == "10"
    assert ps["all"].location == "query-object" and "hdrs" not in ps


def test_qualified_suffix_match_requires_segment_boundary(tmp_path):
    p = make_project(tmp_path, {
        "com/ab/Foo.java": "package com.ab; public class Foo {}",
        "x/User.java": "package x; import org.springframework.web.bind.annotation.*; @RestController public class User { @GetMapping(\"/u\") public b.Foo get() { return null; } }",
    })
    ctx = p.index.context_for("x.User")
    assert not p.index.resolve("b.Foo", ctx).ok


def test_custom_call_steps_with_css_braces(sample_project, tmp_path):
    steps = tmp_path / "steps.html"
    steps.write_text("<style>p{margin:0}</style><p>{method} {path}</p>", encoding="utf-8")
    sample_project.cfg.data["doc"]["call_steps_file"] = str(steps)
    b = DocBuilder(sample_project.cfg, sample_project.index, sample_project.store)
    d = b.build(sample_project.endpoint("POST /Api/Reverse"), 1)
    assert "p{margin:0}" in d.call_steps_html and "POST /Api/Reverse" in d.call_steps_html


def test_static_import_calls_are_resolved(tmp_path):
    p = make_project(tmp_path, {
        "u/Errors.java": "package u; public class Errors { public static RuntimeException fail(int code) { return null; } }",
        "a/C.java": "package a; import static u.Errors.fail; public class C { public void m() { throw fail(1); } }",
    })
    calls = {d for d, k, m in p.store.edges_from("a.C#m()", "calls")}
    assert "u.Errors#fail(int)" in calls


def test_ui_static_path_traversal_is_blocked(sample_project):
    ws = Workspace.load(str(sample_project.cfg.path), None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ws))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        port = httpd.server_address[1]
        for path in ("/static/..%2F..%2F..%2F..%2Fetc%2Fpasswd", "/static/../server.py", "/archive/0/..%2Fgraph.db"):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
                assert False, path
            except urllib.error.HTTPError as e:
                assert e.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_missing_scan_root_keeps_previous_files(sample_project):
    before = sample_project.store.file_count()
    sample_project.cfg.data["project"]["paths"].append("./does-not-exist")
    res = sample_project.scan()
    assert res.removed == [] and sample_project.store.file_count() == before
    assert any("not found" in v for v in res.parse_errors.values())


def test_cross_file_route_constants_are_expanded(tmp_path):
    p = make_project(tmp_path, {
        "a/Routes.java": "package a; public final class Routes {\n"
                         "  public static final String BASE = \"/api/v1\";\n"
                         "  public static final String COMPETITION = \"/competitions\";\n"
                         "  public static final String ID = \"/{id}\";\n"
                         "  public static final String ACTIVATE = COMPETITION + \"/activate\" + ID;\n"
                         "}",
        "a/C.java": "package a; import org.springframework.web.bind.annotation.*;\n"
                    "@RestController @RequestMapping(Routes.BASE) public class C {\n"
                    "  @PostMapping(Routes.COMPETITION) public String create() { return null; }\n"
                    "  @PutMapping(Routes.ACTIVATE) public String activate() { return null; }\n"
                    "}",
    })
    ids = {e.id: e.path for e in p.endpoints()}
    assert ids["POST /api/v1/competitions"] == "/api/v1/competitions"
    assert ids["PUT /api/v1/competitions/activate/{id}"] == "/api/v1/competitions/activate/{id}"


def test_request_mapping_multiple_http_methods(tmp_path):
    p = make_project(tmp_path, {
        "a/C.java": "package a; import org.springframework.web.bind.annotation.*;\n"
                    "@RestController public class C {\n"
                    "  @RequestMapping(value=\"/ping\", method={RequestMethod.GET, RequestMethod.POST}) public String ping() { return null; }\n"
                    "}",
    })
    ids = {e.id for e in p.endpoints()}
    assert ids == {"GET /ping", "POST /ping"}


def test_list_response_is_not_flattened_to_object(tmp_path):
    p = make_project(tmp_path, {
        "a/User.java": "package a; public class User { public String name; }",
        "a/C.java": "package a; import java.util.List; import org.springframework.web.bind.annotation.*;\n"
                    "@RestController public class C { @GetMapping(\"/users\") public List<User> all() { return null; } }",
    })
    b = DocBuilder(p.cfg, p.index, p.store)
    d = b.build(p.endpoints()[0], 1)
    assert d.response_rows[0].is_list and d.response_rows[0].type_str.startswith("List<")
    assert any(t.name == "User" for t in d.nested_tables)


def test_unchanged_file_hash_is_not_rewritten(tmp_path):
    p = make_project(tmp_path, {"a/C.java": "package a; public class C { public int x; }"})
    first = p.store.conn.execute("SELECT scanned_at FROM files").fetchone()[0]
    res = p.scan()
    assert res.unchanged >= 1 and res.added == [] and res.changed == []
    second = p.store.conn.execute("SELECT scanned_at FROM files").fetchone()[0]
    assert second == first


def test_ucs_agent_highlights_new_requirement(tmp_path):
    from apidocgen.agent import AnalystRequest, SeniorAnalyst
    from apidocgen.llm import MockClient

    p = make_project(tmp_path, {
        "a/C.java": "package a; import org.springframework.web.bind.annotation.*;\n"
                    "@RestController public class C { @PostMapping(\"/join\") public String join(@RequestBody String body) { return body; } }",
    })
    analyst = SeniorAnalyst(p, MockClient())
    result = analyst.run(AnalystRequest(mode="new", new_requirement="عضو جدید باید رمز تیم را ارسال کند."))
    assert result.specs
    assert "عضو جدید باید رمز تیم را ارسال کند" in result.html
    assert "new-req" in result.html
    result2 = analyst.run(AnalystRequest(mode="existing", endpoint_id=p.endpoints()[0].id,
                                         previous_analysis="جریان قبلی: کاربر عضو می‌شود.",
                                         new_requirement="رمز تیم اجباری شود."))
    assert "رمز تیم اجباری شود" in result2.html
    assert "جریان قبلی" in result2.html
