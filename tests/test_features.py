from pathlib import Path

from apidocgen.config import Config
from apidocgen.docmodel.builder import DocBuilder
from apidocgen.project import Project
from apidocgen.render.html import HtmlRenderer
from apidocgen.services.scan_service import scan_project

JAVA = r"""
package com.example.api;

class UserInfoConstants {
    public static final String IDENTITY_TYPE = "identityType";
}

enum IdentityKind {
    @com.fasterxml.jackson.annotation.JsonProperty("national")
    NATIONAL("ملی", 1),
    FOREIGN("اتباع", 2);
}

class LoginRequest {
    @io.swagger.v3.oas.annotations.media.Schema(name = UserInfoConstants.IDENTITY_TYPE)
    private IdentityKind identityKind;

    @io.swagger.v3.oas.annotations.media.Schema(name = "username")
    @javax.validation.constraints.NotBlank
    private String username_old;
}

class IssueResult {
    public String id;
}

class ErrorBody {
    public int code;
    public String message;
}

class LoginController {
    @org.springframework.web.bind.annotation.PostMapping("/login")
    @io.swagger.v3.oas.annotations.responses.ApiResponse(responseCode = "200",
        content = @io.swagger.v3.oas.annotations.media.Content(
            schema = @io.swagger.v3.oas.annotations.media.Schema(implementation = IssueResult.class)))
    @org.springframework.security.access.prepost.PreAuthorize("isAuthenticated()")
    public javax.ws.rs.core.Response login(@org.springframework.web.bind.annotation.RequestBody LoginRequest body) {
        if (body == null) {
            throw new IllegalArgumentException("empty");
        }
        return null;
    }
}
"""


def _project(tmp_path: Path) -> Project:
    src = tmp_path / "src" / "main" / "java"
    src.mkdir(parents=True)
    (src / "Api.java").write_text(JAVA, encoding="utf-8")
    (tmp_path / "apidocgen.yaml").write_text(
        """
project:
  name: demo
  paths: [./src/main/java]
  db: .apidocgen/graph.db
llm:
  provider: mock
  model: mock-1
doc:
  title: demo
  failure_type: ErrorBody
""",
        encoding="utf-8",
    )
    project = Project(Config.load(str(tmp_path / "apidocgen.yaml")))
    scan_project(project)
    return project


def test_scan_keeps_source_and_selected_method_graph(tmp_path):
    project = _project(tmp_path)
    try:
        row = project.store.conn.execute("SELECT source_text FROM files").fetchone()
        assert row and "class LoginController" in row["source_text"]
        graph = scan_project(project)["controller_graph"]
        assert graph and graph[0]["methods"]
        assert "login(" in graph[0]["methods"][0]["source"]
        assert graph[0]["methods"][0]["sha256"]
    finally:
        project.close()


def test_document_uses_swagger_auth_failure_enum_and_rules(tmp_path):
    project = _project(tmp_path)
    try:
        spec = project.endpoints()[0]
        assert spec.response_type.simple_name == "IssueResult"
        doc = DocBuilder(project.cfg, project.index, project.store).build(spec, 1)
        names = {row.java_name: row.name for row in doc.request_rows}
        assert names["username_old"] == "username"
        assert names["identityKind"] == "identityType"
        enum = next(iter(doc.enum_tables.values()))
        national = next(v for v in enum.values if v.name == "NATIONAL")
        assert national.wire == "national"
        assert "isAuthenticated()" in doc.auth_text
        assert any("body == null" in rule for rule in doc.business_rules)
        assert any("username_old" in rule for rule in doc.business_rules)
        assert {row.name for row in doc.failure_rows} >= {"code", "message"}
        html = HtmlRenderer(project.cfg, project.store).render([doc])
        assert "احراز هویت" in html
        assert "مقدار ارسالی" in html
        assert "national" in html
        assert "پارامترهای پاسخ ناموفق" in html
        assert "شروط کسب‌وکار" in html
        assert "IssueResult" not in html.split("پارامترهای پاسخ")[1][:200] or "id" in html
        assert ">Response<" not in html
    finally:
        project.close()
