from pathlib import Path

from apidocgen.javaparse import parse_java
from apidocgen.javaparse.parser import parse_javadoc
from apidocgen.javaparse.tokenizer import tokenize, unquote_string

FIXTURE = Path(__file__).parent / "fixtures" / "Tricky.java"


def test_tokenizer_basics():
    toks = tokenize('int a = 0xFFL; String s = "a\\"b"; char c = \'\\\'\'; // hi\n/** doc */ x -> y :: z ...')
    texts = [t.text for t in toks if t.kind != "eof"]
    assert '"a\\"b"' in texts and "0xFFL" in texts and "->" in texts and "::" in texts and "..." in texts
    assert unquote_string('"a\\"b\\n"') == 'a"b\n'
    assert unquote_string('"""\n    hello\n      world\n    """') == "hello\n  world\n"


def test_tricky_file_parses_without_errors():
    jf = parse_java(FIXTURE.read_text(encoding="utf-8"), str(FIXTURE))
    assert jf.errors == []
    assert jf.package == "com.example.tricky"
    names = {t.qname for t in jf.all_types()}
    assert "com.example.tricky.Tricky" in names
    assert "com.example.tricky.Tricky.Channel" in names
    assert "com.example.tricky.Tricky.Pair" in names
    assert "com.example.tricky.PackagePrivate" in names


def test_declarations_and_annotations():
    jf = parse_java(FIXTURE.read_text(encoding="utf-8"))
    tricky = jf.types[0]
    assert tricky.type_params == ["T"]
    assert tricky.extends[0].canonical() == "Base<Map<String, List<Integer>>>"
    rm = next(a for a in tricky.annotations if a.simple_name == "RequestMapping")
    assert rm.args["value"] == ["/API", "/api"]
    assert rm.args["produces"] == "MediaType.APPLICATION_JSON_VALUE"
    fields = {f.name: f for f in tricky.fields}
    assert fields["map"].type.canonical() == "Map<String, List<Integer>>"
    assert fields["matrix"].type.dims == 3
    assert fields["fn"].comment == "trailing comment"
    assert "شناسه ترمینال" in fields["map"].comment
    issue = next(m for m in tricky.methods if m.name == "issue")
    assert [p.name for p in issue.params] == ["request", "apiKey", "verbose", "id", "raw"]
    assert issue.params[2].annotations[0].args["required"] is False
    assert issue.javadoc.params["request"] == "the request body"
    assert issue.return_type.canonical() == "ResponseEntity<ApiResponse<IssueResult>>"
    assert [t.canonical() for t in issue.throws] == ["BusinessException", "java.io.IOException"]
    generic = next(m for m in tricky.methods if m.name == "generic")
    assert generic.type_params == ["U"] and generic.params[1].varargs


def test_body_scan():
    jf = parse_java(FIXTURE.read_text(encoding="utf-8"))
    issue = next(m for m in jf.types[0].methods if m.name == "issue")
    body = issue.body
    names = {(tuple(c.receiver), c.name) for c in body.calls}
    assert (("raw",), "getParameter") in names
    assert (("documentService",), "issue") in names
    assert (("ResponseEntity",), "ok") in names
    assert body.throws[0].type_name == "BusinessException"
    assert "ErrorCode.INVALID_INPUT" in body.throws[0].refs
    assert "TransactionChannel.POS" in body.refs
    assert body.locals.get("items") == "List" and body.locals.get("res") == "IssueResult"
    call = next(c for c in body.calls if c.name == "getParameter")
    assert call.string_args == ["page"]
    pp = jf.types[1].methods[0].body
    assert "handler::get" in next(c.method_refs for c in pp.calls if c.name == "route")


def test_enum_and_record():
    jf = parse_java(FIXTURE.read_text(encoding="utf-8"))
    ch = next(t for t in jf.all_types() if t.name == "Channel")
    assert [c.name for c in ch.enum_constants] == ["POS", "ATM", "INTERNET", "TELEPHONE"]
    assert ch.enum_constants[0].args == ["1", '"پایانه فروش"']
    assert ch.enum_constants[0].javadoc.text == "point of sale"
    assert ch.enum_constants[2].comment == "internet banking"
    pair = next(t for t in jf.all_types() if t.name == "Pair")
    assert [p.name for p in pair.record_components] == ["first", "second"]
    assert pair.record_components[0].annotations[0].name == "NotNull"


def test_javadoc_parsing():
    jd = parse_javadoc("/**\n * Line one.\n * Line two {@code x}.\n * @param a first\n *   continued\n * @return value\n * @throws X when\n * @deprecated use Y\n */")
    assert jd.text == "Line one. Line two x."
    assert jd.params["a"] == "first continued"
    assert jd.returns == "value"
    assert jd.throws["X"] == "when"
    assert jd.tags["deprecated"] == "use Y"


def test_recovery_from_garbage():
    src = "package a; public class A { int x = ; public void ok() { } garbage garbage ( ; public int y; }"
    jf = parse_java(src)
    a = jf.types[0]
    assert any(m.name == "ok" for m in a.methods)
    assert jf.errors  # the broken member was reported, parsing continued


def test_stress_file_parses_cleanly():
    src = (Path(__file__).parent / "fixtures" / "Stress.java").read_text(encoding="utf-8")
    jf = parse_java(src, "Stress.java")
    assert jf.errors == []
    st = jf.types[0]
    assert st.type_params == ["K", "V"]
    fields = {f.name: f for f in st.fields}
    assert fields["other"].type.dims == 2 and fields["tricky"].initializer.startswith('"}{ //')
    assert fields["flag"].initializer == "1 < 2 && 3 > 2"
    names = {t.name for t in jf.all_types()}
    assert {"Meta", "Nested", "Shape", "Circle", "Square", "Op", "Visitor", "Deep", "SN", "Empty", "Ann"} <= names
    op = next(t for t in jf.all_types() if t.name == "Op")
    assert [c.name for c in op.enum_constants] == ["PLUS", "MINUS"] and op.enum_constants[0].args == ['"+"']
    pick = next(m for m in st.methods if m.name == "pick")
    assert pick.params[1].varargs and "IllegalStateException" in pick.body.creations
