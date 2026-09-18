from apidocgen.docmodel.fields import FieldExpander
from apidocgen.graph.index import CodeIndex
from apidocgen.javaparse import parse_java


SWAGGER_SRC = """
package com.example.api;

class UserInfoConstants {
    public static final String IDENTITY_TYPE = "identityType";
}

class LoginRequest {
    @io.swagger.v3.oas.annotations.media.Schema(name = UserInfoConstants.IDENTITY_TYPE)
    private String identityKind;

    @io.swagger.v3.oas.annotations.media.Schema(name = "username")
    private String username_old;
}

class IssueResult {
    public String id;
}

class LoginController {
    @org.springframework.web.bind.annotation.PostMapping("/login")
    @io.swagger.v3.oas.annotations.responses.ApiResponse(responseCode = "200",
        content = @io.swagger.v3.oas.annotations.media.Content(
            schema = @io.swagger.v3.oas.annotations.media.Schema(implementation = IssueResult.class)))
    @org.springframework.security.access.prepost.PreAuthorize("isAuthenticated()")
    public javax.ws.rs.core.Response login(@org.springframework.web.bind.annotation.RequestBody LoginRequest body) {
        return null;
    }
}
"""


def test_swagger_wire_names_and_constants():
    jf = parse_java(SWAGGER_SRC, "Login.java")
    index = CodeIndex([jf])
    expander = FieldExpander(index)
    rows = {row.java_name: row.name for row in expander.rows_for_type("com.example.api.LoginRequest")}
    assert rows["username_old"] == "username"
    assert rows["identityKind"] == "identityType"


def test_swagger_response_and_auth():
    from apidocgen.detectors.annotation_frameworks import AnnotationFrameworkDetector, SPRING

    jf = parse_java(SWAGGER_SRC, "Login.java")
    index = CodeIndex([jf])
    detector = AnnotationFrameworkDetector(SPRING)
    specs = detector.detect(index.types["com.example.api.LoginController"], jf, index)
    assert specs
    spec = specs[0]
    assert spec.response_type is not None
    assert spec.response_type.simple_name == "IssueResult"
    assert spec.auth.get("scheme") == "preauthorize"
    assert "isAuthenticated()" in spec.auth.get("note", "")
