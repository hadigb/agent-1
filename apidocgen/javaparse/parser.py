"""Declaration-level Java parser (pure Python, no native dependencies).

The parser understands the *structure* of Java source files - packages,
imports, classes / interfaces / enums / records / annotation types, fields,
methods, constructors, annotations, generics, javadoc and ordinary comments -
and treats method bodies as opaque token streams that are *scanned* for the
information the code graph needs (method calls, object creations, throw
statements, qualified constant references, type mentions, string literals).

That is exactly the level of detail needed to build an endpoint/DTO code graph
and document API surfaces, and it is very forgiving: an unparsable member is
recorded as an error and skipped, the rest of the file is still used.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .model import (
    Annotation, BodyInfo, CallSite, EnumConstant, FieldDecl, ImportDecl, JavaFile, Javadoc,
    MethodDecl, Param, ThrowSite, TypeDecl, TypeRef,
)
from .tokenizer import (
    CHAR, COMMENT, EOF, IDENT, JAVADOC, MODIFIERS, NUMBER, OP, PRIMITIVES, STRING, Token, tokenize,
    unquote_string,
)

TYPE_KEYWORDS = {"class", "interface", "enum", "record"}
_STOP_KEYWORDS = {"if", "for", "while", "switch", "catch", "synchronized", "return", "new", "super",
                  "this", "try", "do", "else", "case", "throw", "throws", "instanceof"}


class ParseError(Exception):
    pass


class JavaParser:
    def __init__(self, src: str, path: str = "<memory>") -> None:
        self.src = src
        self.path = path
        self.toks: List[Token] = tokenize(src)
        self.pos = 0
        self.errors: List[str] = []
        self.package = ""

    # ------------------------------------------------------------------ #
    # cursor helpers (skip comments)
    # ------------------------------------------------------------------ #
    def _skip_comments(self, i: int) -> int:
        toks = self.toks
        while toks[i].kind in (COMMENT, JAVADOC):
            i += 1
        return i

    def peek(self, k: int = 0) -> Token:
        i = self._skip_comments(self.pos)
        while k > 0:
            if self.toks[i].kind == EOF:
                return self.toks[i]
            i = self._skip_comments(i + 1)
            k -= 1
        return self.toks[i]

    def peek_index(self) -> int:
        return self._skip_comments(self.pos)

    def next(self) -> Token:
        i = self._skip_comments(self.pos)
        t = self.toks[i]
        if t.kind != EOF:
            self.pos = i + 1
        else:
            self.pos = i
        return t

    def at(self, text: str) -> bool:
        return self.peek().text == text

    def at_kind(self, kind: str) -> bool:
        return self.peek().kind == kind

    def accept(self, text: str) -> bool:
        if self.at(text):
            self.next()
            return True
        return False

    def expect(self, text: str) -> Token:
        t = self.peek()
        if t.text != text:
            raise ParseError(f"expected {text!r} but found {t.text!r} at line {t.line}")
        return self.next()

    def expect_ident(self) -> Token:
        t = self.peek()
        if t.kind != IDENT:
            raise ParseError(f"expected identifier but found {t.text!r} at line {t.line}")
        return self.next()

    def eof(self) -> bool:
        return self.peek().kind == EOF

    # ------------------------------------------------------------------ #
    # balanced skipping
    # ------------------------------------------------------------------ #
    _PAIRS = {"{": "}", "(": ")", "[": "]"}

    def skip_balanced(self) -> Tuple[int, int]:
        """Current token must be an opener; skip to its matching closer.

        Returns raw token indices (open_index, close_index) - inclusive.
        """
        i = self.peek_index()
        opener = self.toks[i].text
        closer = self._PAIRS[opener]
        depth = 0
        toks = self.toks
        n = len(toks)
        j = i
        while j < n:
            t = toks[j]
            if t.kind == OP:
                if t.text == opener:
                    depth += 1
                elif t.text == closer:
                    depth -= 1
                    if depth == 0:
                        self.pos = j + 1
                        return i, j
            elif t.kind == EOF:
                break
            j += 1
        raise ParseError(f"unbalanced {opener!r} starting at line {toks[i].line}")

    def _try_skip_type_args(self, i: int) -> Optional[int]:
        """If raw index ``i`` points at a '<' that starts a plausible type-argument
        list, return the raw index of the matching '>' else None."""
        toks = self.toks
        if toks[i].text != "<":
            return None
        depth = 0
        j = i
        n = len(toks)
        while j < n:
            t = toks[j]
            if t.kind in (COMMENT, JAVADOC):
                j += 1
                continue
            if t.kind == OP:
                if t.text == "<":
                    depth += 1
                elif t.text == ">":
                    depth -= 1
                    if depth == 0:
                        return j
                elif t.text in (",", ".", "?", "&", "[", "]", "@"):
                    pass
                else:
                    return None
            elif t.kind == IDENT:
                pass
            else:
                return None
            j += 1
        return None

    # ------------------------------------------------------------------ #
    # comments attached to declarations
    # ------------------------------------------------------------------ #
    def _comments_between(self, start_raw: int, end_raw: int) -> Tuple[Optional[Javadoc], str]:
        javadoc: Optional[Javadoc] = None
        plain: List[str] = []
        for k in range(start_raw, end_raw):
            t = self.toks[k]
            if t.kind == JAVADOC:
                javadoc = parse_javadoc(t.text)
                plain = []
            elif t.kind == COMMENT:
                plain.append(clean_comment(t.text))
        return javadoc, " ".join(p for p in plain if p).strip()

    def _trailing_comment(self, line: int) -> str:
        """Consume a comment that sits on the same line right after the cursor."""
        i = self.pos
        if i < len(self.toks) and self.toks[i].kind == COMMENT and self.toks[i].line == line:
            self.pos = i + 1
            return clean_comment(self.toks[i].text)
        return ""

    # ------------------------------------------------------------------ #
    # compilation unit
    # ------------------------------------------------------------------ #
    def parse(self) -> JavaFile:
        jf = JavaFile(path=self.path)
        try:
            self._parse_unit(jf)
        except ParseError as e:  # pragma: no cover - top level guard
            self.errors.append(str(e))
        except Exception as e:  # pragma: no cover - never let one file kill a scan
            self.errors.append(f"internal parser error: {e!r}")
        jf.errors = self.errors
        return jf

    def _parse_unit(self, jf: JavaFile) -> None:
        # package
        while not self.eof():
            t = self.peek()
            if t.text == "package":
                self.next()
                jf.package = self._parse_qualified_name()
                self.accept(";")
                self.package = jf.package
            elif t.text == "import":
                self.next()
                static = self.accept("static")
                name = self._parse_qualified_name(allow_star=True)
                wildcard = name.endswith(".*")
                if wildcard:
                    name = name[:-2]
                jf.imports.append(ImportDecl(name=name, static=static, wildcard=wildcard))
                self.accept(";")
            elif t.text == ";":
                self.next()
            elif t.text == "@" and self.peek(1).text != "interface":
                break
            else:
                break
        last_end = self.pos
        while not self.eof():
            if self.accept(";"):
                last_end = self.pos
                continue
            start_raw = self.peek_index()
            javadoc, comment = self._comments_between(last_end, start_raw)
            try:
                mods, annos = self._parse_modifiers()
                t = self.peek()
                if t.text in TYPE_KEYWORDS or (t.text == "@" and self.peek(1).text == "interface"):
                    decl = self._parse_type_decl(None, javadoc, comment, mods, annos, start_raw)
                    jf.types.append(decl)
                elif t.kind == EOF:
                    break
                else:
                    raise ParseError(f"unexpected token {t.text!r} at line {t.line}")
            except ParseError as e:
                self.errors.append(str(e))
                self._recover()
            last_end = self.pos

    def _parse_qualified_name(self, allow_star: bool = False) -> str:
        parts = [self.expect_ident().text]
        while self.at("."):
            self.next()
            if allow_star and self.at("*"):
                self.next()
                parts.append("*")
                break
            parts.append(self.expect_ident().text)
        return ".".join(parts)

    def _recover(self) -> None:
        """Skip to the end of the current member (';' or a balanced block)."""
        while not self.eof():
            t = self.peek()
            if t.text == ";":
                self.next()
                return
            if t.text == "{":
                try:
                    self.skip_balanced()
                except ParseError:
                    self.pos = len(self.toks) - 1
                return
            if t.text == "}":
                return
            if t.text in ("(", "["):
                try:
                    self.skip_balanced()
                    continue
                except ParseError:
                    self.pos = len(self.toks) - 1
                    return
            self.next()

    # ------------------------------------------------------------------ #
    # modifiers & annotations
    # ------------------------------------------------------------------ #
    def _parse_modifiers(self) -> Tuple[List[str], List[Annotation]]:
        mods: List[str] = []
        annos: List[Annotation] = []
        while True:
            t = self.peek()
            if t.text == "@" and self.peek(1).text != "interface":
                annos.append(self._parse_annotation())
            elif t.kind == IDENT and t.text in MODIFIERS:
                # 'default' inside a switch never reaches member level; 'sealed'/'non-sealed' are contextual
                if t.text == "default" and self.peek(1).text == ":":
                    break
                mods.append(t.text)
                self.next()
            else:
                break
        return mods, annos

    def _parse_annotation(self) -> Annotation:
        at_tok = self.expect("@")
        name = self._parse_qualified_name()
        anno = Annotation(name=name, line=at_tok.line)
        if self.at("("):
            open_i = self.peek_index()
            self.next()
            if self.at(")"):
                self.next()
            else:
                # named pairs or single value
                if self.peek().kind == IDENT and self.peek(1).text == "=":
                    while True:
                        key = self.expect_ident().text
                        self.expect("=")
                        anno.args[key] = self._parse_element_value()
                        if self.accept(","):
                            continue
                        break
                    self.expect(")")
                else:
                    anno.args["value"] = self._parse_element_value()
                    # tolerate trailing junk
                    if not self.at(")"):
                        self._skip_until_depth0((")",))
                    self.expect(")")
            close_i = self.pos - 1
            anno.raw = self.src[self.toks[open_i].start:self.toks[close_i].end]
        return anno

    def _skip_until_depth0(self, terminators: Tuple[str, ...]) -> int:
        """Advance until one of ``terminators`` at depth 0 (not consumed). Returns raw index of the last consumed token."""
        depth = 0
        last = self.pos
        while not self.eof():
            t = self.peek()
            if depth == 0 and t.text in terminators:
                return last
            if t.text in ("(", "{", "["):
                depth += 1
            elif t.text in (")", "}", "]"):
                if depth == 0:
                    return last
                depth -= 1
            last = self.peek_index()
            self.next()
        return last

    def _parse_element_value(self) -> Any:
        t = self.peek()
        if t.text == "{":
            self.next()
            items: List[Any] = []
            while not self.at("}") and not self.eof():
                items.append(self._parse_element_value())
                if not self.accept(","):
                    break
            self.expect("}")
            return items
        if t.text == "@":
            nested = self._parse_annotation()
            return {"@": nested.name, **nested.args}
        # generic expression: capture until ',' or ')' or '}' at depth 0
        start_i = self.peek_index()
        end_i = self._skip_until_depth0((",", ")", "}"))
        toks = [x for x in self.toks[start_i:end_i + 1] if x.kind not in (COMMENT, JAVADOC)]
        raw = self.src[self.toks[start_i].start:self.toks[end_i].end] if toks else ""
        return decode_expression(toks, raw)

    # ------------------------------------------------------------------ #
    # types
    # ------------------------------------------------------------------ #
    def parse_type(self) -> TypeRef:
        annos: List[str] = []
        while self.at("@"):
            a = self._parse_annotation()
            annos.append(a.simple_name)
        t = self.peek()
        if t.kind != IDENT:
            raise ParseError(f"expected type but found {t.text!r} at line {t.line}")
        if t.text == "?":
            pass
        if t.text in PRIMITIVES:
            self.next()
            ref = TypeRef(name=t.text, annotations=annos)
        else:
            parts = [self.next().text]
            args: List[TypeRef] = []
            if self.at("<"):
                args = self._parse_type_args()
            while self.at(".") and self.peek(1).kind == IDENT:
                self.next()
                parts.append(self.next().text)
                if self.at("<"):
                    args = self._parse_type_args()
            ref = TypeRef(name=".".join(parts), args=args, annotations=annos)
        while self.at("[") and self.peek(1).text == "]":
            self.next()
            self.next()
            ref.dims += 1
        return ref

    def _parse_type_args(self) -> List[TypeRef]:
        self.expect("<")
        args: List[TypeRef] = []
        if self.at(">"):  # diamond
            self.next()
            return args
        while True:
            while self.at("@"):
                self._parse_annotation()
            if self.at("?"):
                self.next()
                if self.at("extends") or self.at("super"):
                    bound_kind = self.next().text
                    bound = self.parse_type()
                    while self.at("&"):
                        self.next()
                        self.parse_type()
                    args.append(TypeRef(name="?", wildcard=f"? {bound_kind}", args=[bound]))
                else:
                    args.append(TypeRef(name="?", wildcard="?"))
            else:
                args.append(self.parse_type())
            if self.accept(","):
                continue
            break
        self.expect(">")
        return args

    def _parse_type_params(self) -> List[str]:
        """Parse ``<T, U extends X<T>>`` returning the parameter names."""
        self.expect("<")
        names: List[str] = []
        depth = 1
        expect_name = True
        while not self.eof():
            t = self.next()
            if t.text == "<":
                depth += 1
            elif t.text == ">":
                depth -= 1
                if depth == 0:
                    break
            elif t.text == "," and depth == 1:
                expect_name = True
            elif t.kind == IDENT and expect_name and depth == 1 and t.text != "extends":
                names.append(t.text)
                expect_name = False
        return names

    # ------------------------------------------------------------------ #
    # type declarations
    # ------------------------------------------------------------------ #
    def _parse_type_decl(self, outer: Optional[TypeDecl], javadoc: Optional[Javadoc], comment: str,
                         mods: List[str], annos: List[Annotation], start_raw: int) -> TypeDecl:
        t = self.next()
        if t.text == "@":
            self.expect("interface")
            kind = "annotation"
        else:
            kind = t.text
        name = self.expect_ident().text
        qname = f"{outer.qname}.{name}" if outer else (f"{self.package}.{name}" if self.package else name)
        decl = TypeDecl(kind=kind, name=name, qname=qname, modifiers=mods, annotations=annos,
                        javadoc=javadoc, comment=comment, outer_qname=outer.qname if outer else None)
        decl.start_line = self.toks[start_raw].line
        if self.at("<"):
            decl.type_params = self._parse_type_params()
        if kind == "record" and self.at("("):
            decl.record_components = self._parse_params()
        while True:
            if self.accept("extends"):
                decl.extends.append(self.parse_type())
                while self.accept(","):
                    decl.extends.append(self.parse_type())
            elif self.accept("implements"):
                decl.implements.append(self.parse_type())
                while self.accept(","):
                    decl.implements.append(self.parse_type())
            elif self.accept("permits"):
                self.parse_type()
                while self.accept(","):
                    self.parse_type()
            else:
                break
        open_i = self.peek_index()
        if self.toks[open_i].text != "{":
            raise ParseError(f"expected '{{' for {kind} {name} at line {self.toks[open_i].line}")
        decl.header_source = self.src[self.toks[start_raw].start:self.toks[open_i].start].strip()
        self.next()  # consume '{'
        if kind == "enum":
            self._parse_enum_constants(decl)
        self._parse_body_members(decl)
        close = self.expect("}")
        decl.end_line = close.line
        decl.source = self.src[self.toks[start_raw].start:close.end]
        return decl

    def _parse_enum_constants(self, decl: TypeDecl) -> None:
        last_end = self.pos
        while not self.eof():
            if self.at(";"):
                self.next()
                return
            if self.at("}"):
                return
            start_raw = self.peek_index()
            javadoc, comment = self._comments_between(last_end, start_raw)
            _mods, annos = self._parse_modifiers()
            t = self.peek()
            if t.kind != IDENT:
                raise ParseError(f"bad enum constant at line {t.line}")
            const = EnumConstant(name=self.next().text, annotations=annos, javadoc=javadoc, comment=comment, line=t.line)
            if self.at("("):
                open_i, close_i = self.skip_balanced()
                const.args = self._split_args(open_i, close_i)
            if self.at("{"):
                self.skip_balanced()
            const.comment = (const.comment + " " + self._trailing_comment(self.toks[self.pos - 1].line)).strip()
            decl.enum_constants.append(const)
            if self.accept(","):
                const.comment = (const.comment + " " + self._trailing_comment(self.toks[self.pos - 1].line)).strip()
                last_end = self.pos
                continue
            if self.accept(";"):
                const.comment = (const.comment + " " + self._trailing_comment(self.toks[self.pos - 1].line)).strip()
                return
            if self.at("}"):
                return
            raise ParseError(f"unexpected {self.peek().text!r} in enum body at line {self.peek().line}")

    def _split_args(self, open_i: int, close_i: int) -> List[str]:
        """Split the raw tokens between '(' and ')' into argument source strings."""
        args: List[str] = []
        depth = 0
        cur_start: Optional[int] = None
        cur_end: Optional[int] = None
        for k in range(open_i + 1, close_i):
            t = self.toks[k]
            if t.kind in (COMMENT, JAVADOC):
                continue
            if t.text in ("(", "{", "["):
                depth += 1
            elif t.text in (")", "}", "]"):
                depth -= 1
            if t.text == "," and depth == 0:
                if cur_start is not None:
                    args.append(self.src[cur_start:cur_end].strip())
                cur_start = None
                continue
            if cur_start is None:
                cur_start = t.start
            cur_end = t.end
        if cur_start is not None:
            args.append(self.src[cur_start:cur_end].strip())
        return args

    def _parse_body_members(self, decl: TypeDecl) -> None:
        last_end = self.pos
        while not self.eof() and not self.at("}"):
            if self.accept(";"):
                last_end = self.pos
                continue
            start_raw = self.peek_index()
            try:
                self._parse_member(decl, start_raw, last_end)
            except ParseError as e:
                self.errors.append(f"{self.path}: {e}")
                self._recover()
            last_end = self.pos

    def _parse_member(self, decl: TypeDecl, start_raw: int, last_end: int) -> None:
        javadoc, comment = self._comments_between(last_end, start_raw)
        mods, annos = self._parse_modifiers()
        t = self.peek()
        # nested type
        if t.text in TYPE_KEYWORDS or (t.text == "@" and self.peek(1).text == "interface"):
            nested = self._parse_type_decl(decl, javadoc, comment, mods, annos, start_raw)
            decl.nested.append(nested)
            return
        # initializer block
        if t.text == "{":
            self.skip_balanced()
            return
        type_params: List[str] = []
        if t.text == "<":
            type_params = self._parse_type_params()
            t = self.peek()
        # constructor
        if t.kind == IDENT and t.text == decl.name and self.peek(1).text == "(":
            self.next()
            m = MethodDecl(name=decl.name, return_type=None, modifiers=mods, annotations=annos,
                           javadoc=javadoc, comment=comment, type_params=type_params, is_constructor=True)
            m.start_line = self.toks[start_raw].line
            m.params = self._parse_params()
            self._parse_throws(m)
            self._finish_method(m, start_raw)
            decl.constructors.append(m)
            return
        # compact record constructor
        if decl.kind == "record" and t.kind == IDENT and t.text == decl.name and self.peek(1).text == "{":
            self.next()
            self.skip_balanced()
            return
        rtype = self.parse_type()
        name_tok = self.expect_ident()
        if self.at("("):
            m = MethodDecl(name=name_tok.text, return_type=rtype, modifiers=mods, annotations=annos,
                           javadoc=javadoc, comment=comment, type_params=type_params)
            m.start_line = self.toks[start_raw].line
            m.params = self._parse_params()
            while self.at("[") and self.peek(1).text == "]":
                self.next()
                self.next()
                rtype.dims += 1
            self._parse_throws(m)
            self._finish_method(m, start_raw)
            decl.methods.append(m)
            return
        # field(s)
        self._parse_fields(decl, rtype, name_tok, mods, annos, javadoc, comment, start_raw)

    def _parse_throws(self, m: MethodDecl) -> None:
        if self.accept("throws"):
            m.throws.append(self.parse_type())
            while self.accept(","):
                m.throws.append(self.parse_type())

    def _finish_method(self, m: MethodDecl, start_raw: int) -> None:
        sig_end = self.peek_index()
        m.signature_source = self.src[self.toks[start_raw].start:self.toks[sig_end].start].strip()
        if self.at("{"):
            open_i, close_i = self.skip_balanced()
            m.body = scan_body(self.toks, self.src, open_i, close_i)
            m.end_line = self.toks[close_i].line
            m.source = self.src[self.toks[start_raw].start:self.toks[close_i].end]
        elif self.accept("default"):
            self._skip_until_depth0((";",))
            end_i = self.peek_index()
            self.expect(";")
            m.end_line = self.toks[end_i].line
            m.source = self.src[self.toks[start_raw].start:self.toks[end_i].end]
        else:
            end_i = self.peek_index()
            self.expect(";")
            m.end_line = self.toks[end_i].line
            m.source = self.src[self.toks[start_raw].start:self.toks[end_i].end]

    def _parse_params(self) -> List[Param]:
        self.expect("(")
        params: List[Param] = []
        while not self.at(")") and not self.eof():
            mods, annos = self._parse_modifiers()
            ptype = self.parse_type()
            varargs = False
            if self.accept("..."):
                varargs = True
            # receiver parameter "Foo this" is rare; treat like a normal name
            name = self.expect_ident().text
            while self.at("[") and self.peek(1).text == "]":
                self.next()
                self.next()
                ptype.dims += 1
            params.append(Param(name=name, type=ptype, annotations=annos, varargs=varargs, modifiers=mods))
            if not self.accept(","):
                break
        self.expect(")")
        return params

    def _parse_fields(self, decl: TypeDecl, ftype: TypeRef, name_tok: Token, mods: List[str],
                      annos: List[Annotation], javadoc: Optional[Javadoc], comment: str, start_raw: int) -> None:
        names: List[Tuple[str, int, str]] = []  # (name, extra dims, initializer)
        cur_name = name_tok.text
        while True:
            dims = 0
            while self.at("[") and self.peek(1).text == "]":
                self.next()
                self.next()
                dims += 1
            init = ""
            if self.accept("="):
                init = self._scan_initializer()
            names.append((cur_name, dims, init))
            if self.accept(","):
                cur_name = self.expect_ident().text
                continue
            break
        end_i = self.peek_index()
        self.expect(";")
        end_line = self.toks[end_i].line
        source = self.src[self.toks[start_raw].start:self.toks[end_i].end]
        trailing = self._trailing_comment(end_line)
        full_comment = " ".join(x for x in (comment, trailing) if x).strip()
        for name, dims, init in names:
            t = TypeRef(name=ftype.name, args=ftype.args, dims=ftype.dims + dims, annotations=ftype.annotations)
            decl.fields.append(FieldDecl(name=name, type=t, modifiers=list(mods), annotations=list(annos),
                                         javadoc=javadoc, comment=full_comment, initializer=init,
                                         start_line=self.toks[start_raw].line, end_line=end_line, source=source))

    def _scan_initializer(self) -> str:
        """Consume an initializer expression up to ',' or ';' at depth 0 (generics aware)."""
        depth = 0
        start_i = self.peek_index()
        last_i = start_i
        toks = self.toks
        while not self.eof():
            i = self.peek_index()
            t = toks[i]
            if depth == 0 and t.text in (",", ";"):
                break
            if t.text in ("(", "{", "["):
                depth += 1
            elif t.text in (")", "}", "]"):
                if depth == 0:
                    break
                depth -= 1
            elif t.text == "<" and depth == 0:
                prev = toks[i - 1] if i > 0 else None
                if prev is not None and (prev.kind == IDENT or prev.text == ">"):
                    j = self._try_skip_type_args(i)
                    if j is not None:
                        self.pos = j + 1
                        last_i = j
                        continue
            last_i = i
            self.next()
        return self.src[toks[start_i].start:toks[last_i].end].strip()


# ---------------------------------------------------------------------- #
# method body scanning
# ---------------------------------------------------------------------- #
def scan_body(toks: List[Token], src: str, open_i: int, close_i: int) -> BodyInfo:
    info = BodyInfo()
    info_type_mentions = set()
    body = [t for t in toks[open_i + 1:close_i] if t.kind not in (COMMENT, JAVADOC)]
    n = len(body)

    def find_close(k: int) -> int:
        """k is index in ``body`` of an opener; return index of matching closer."""
        opener = body[k].text
        closer = {"(": ")", "{": "}", "[": "]"}[opener]
        depth = 0
        j = k
        while j < n:
            tt = body[j].text
            if body[j].kind == OP:
                if tt == opener:
                    depth += 1
                elif tt == closer:
                    depth -= 1
                    if depth == 0:
                        return j
            j += 1
        return n - 1

    i = 0
    while i < n:
        t = body[i]
        if t.kind == STRING:
            if len(info.string_literals) < 300:
                info.string_literals.append(unquote_string(t.text))
            i += 1
            continue
        if t.kind != IDENT:
            i += 1
            continue
        nxt = body[i + 1] if i + 1 < n else None
        prev = body[i - 1] if i > 0 else None
        # object creation
        if t.text == "new":
            j = i + 1
            parts = []
            while j < n and body[j].kind == IDENT:
                parts.append(body[j].text)
                j += 1
                if j < n and body[j].text == "." and j + 1 < n and body[j + 1].kind == IDENT:
                    j += 1
                    continue
                break
            if parts:
                # skip generic args
                if j < n and body[j].text == "<":
                    depth = 0
                    while j < n:
                        if body[j].text == "<":
                            depth += 1
                        elif body[j].text == ">":
                            depth -= 1
                            if depth == 0:
                                j += 1
                                break
                        j += 1
                tname = ".".join(parts)
                if j < n and body[j].text == "(":
                    info.creations.append(tname)
                info_type_mentions.add(parts[-1] if len(parts) == 1 else tname)
            i = max(j, i + 1)
            continue
        # throw statements
        if t.text == "throw":
            j = i + 1
            depth = 0
            end = j
            while end < n:
                tt = body[end].text
                if depth == 0 and tt == ";":
                    break
                if tt in ("(", "{", "["):
                    depth += 1
                elif tt in (")", "}", "]"):
                    depth -= 1
                end += 1
            stmt = body[j:end]
            ts = ThrowSite(type_name=None, args_raw="", line=t.line)
            if stmt and stmt[0].text == "new":
                k = 1
                parts = []
                while k < len(stmt) and stmt[k].kind == IDENT:
                    parts.append(stmt[k].text)
                    k += 1
                    if k < len(stmt) and stmt[k].text == "." and k + 1 < len(stmt) and stmt[k + 1].kind == IDENT:
                        k += 1
                        continue
                    break
                ts.type_name = ".".join(parts) if parts else None
                if k < len(stmt) and stmt[k].text == "(":
                    ts.args_raw = src[stmt[k].start:stmt[-1].end] if stmt else ""
                if parts:
                    info_type_mentions.add(parts[-1])
            else:
                ts.args_raw = src[stmt[0].start:stmt[-1].end] if stmt else ""
            ts.refs = _qualified_refs(stmt)
            info.throws.append(ts)
            # also scan the statement for calls (e.g. new X(service.code()))
            i += 1
            continue
        # method call
        if nxt is not None and nxt.text == "(" and t.text not in _STOP_KEYWORDS:
            receiver: List[str] = []
            k = i - 1
            if k >= 0 and body[k].text == ".":
                k -= 1
                while k >= 0:
                    tk = body[k]
                    if tk.kind == IDENT:
                        receiver.insert(0, tk.text)
                        if k - 1 >= 0 and body[k - 1].text == "." and k - 2 >= 0 and body[k - 2].kind == IDENT:
                            k -= 2
                            continue
                        break
                    if tk.text in (")", "]"):
                        receiver.insert(0, "()")
                        break
                    if tk.text == ">":  # explicit generic call this.<T>foo()
                        receiver.insert(0, "()")
                        break
                    break
            close = find_close(i + 1)
            args = body[i + 2:close]
            argc = 0
            depth = 0
            if args:
                argc = 1
                for a in args:
                    if a.text in ("(", "{", "["):
                        depth += 1
                    elif a.text in (")", "}", "]"):
                        depth -= 1
                    elif a.text == "," and depth == 0:
                        argc += 1
            cs = CallSite(name=t.text, receiver=receiver, argc=argc, line=t.line)
            depth = 0
            for idx, a in enumerate(args):
                if a.text in ("(", "{", "["):
                    depth += 1
                elif a.text in (")", "}", "]"):
                    depth -= 1
                elif depth == 0:
                    if a.kind == STRING:
                        cs.string_args.append(unquote_string(a.text))
                    elif a.text == "class" and idx >= 2 and args[idx - 1].text == "." and args[idx - 2].kind == IDENT:
                        cs.class_args.append(args[idx - 2].text)
                    elif a.text == "::" and idx >= 1 and idx + 1 < len(args):
                        cs.method_refs.append(f"{args[idx - 1].text}::{args[idx + 1].text}")
            info.calls.append(cs)
            if t.text[:1].isupper() and (prev is None or prev.text != "."):
                info_type_mentions.add(t.text)
            i += 1  # continue scanning inside the arguments
            continue
        # qualified references & type mentions
        if t.text[:1].isupper() and (prev is None or prev.text != "."):
            info_type_mentions.add(t.text)
            if nxt is not None and nxt.text == "." and i + 2 < n and body[i + 2].kind == IDENT:
                after = body[i + 3].text if i + 3 < n else ""
                if after != "(":
                    ref = f"{t.text}.{body[i + 2].text}"
                    if ref not in info.refs:
                        info.refs.append(ref)
            # local variable declaration:  Type name = / ; / :   (generics and arrays tolerated)
            j = i + 1
            if j < n and body[j].text == "<":
                depth = 0
                while j < n:
                    if body[j].text == "<":
                        depth += 1
                    elif body[j].text == ">":
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                    j += 1
            while j + 1 < n and body[j].text == "[" and body[j + 1].text == "]":
                j += 2
            if j + 1 < n and body[j].kind == IDENT and body[j].text not in _STOP_KEYWORDS \
                    and body[j + 1].text in ("=", ";", ":", ","):
                if body[j].text not in info.locals:
                    info.locals[body[j].text] = t.text
        elif t.text == "var" and nxt is not None and nxt.kind == IDENT and i + 3 < n and body[i + 2].text == "=" \
                and body[i + 3].text == "new" and i + 4 < n and body[i + 4].kind == IDENT:
            info.locals.setdefault(nxt.text, body[i + 4].text)
        i += 1
    info.type_mentions = sorted(info_type_mentions)
    return info


def _qualified_refs(stmt: List[Token]) -> List[str]:
    refs: List[str] = []
    for idx, t in enumerate(stmt):
        if t.kind == IDENT and t.text[:1].isupper() and (idx == 0 or stmt[idx - 1].text != "."):
            if idx + 2 < len(stmt) and stmt[idx + 1].text == "." and stmt[idx + 2].kind == IDENT:
                after = stmt[idx + 3].text if idx + 3 < len(stmt) else ""
                if after != "(":
                    refs.append(f"{t.text}.{stmt[idx + 2].text}")
    return refs


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #
def decode_expression(toks: List[Token], raw: str) -> Any:
    """Best effort decoding of an annotation element value."""
    if not toks:
        return raw
    if len(toks) == 1:
        t = toks[0]
        if t.kind == STRING:
            return unquote_string(t.text)
        if t.kind == NUMBER:
            return _decode_number(t.text)
        if t.kind == CHAR:
            return unquote_string('"' + t.text[1:-1] + '"') if len(t.text) >= 2 else t.text
        if t.text == "true":
            return True
        if t.text == "false":
            return False
        return t.text
    # "a" + "b" concatenation of pure strings
    if all((t.kind == STRING) or (t.kind == OP and t.text == "+") for t in toks):
        return "".join(unquote_string(t.text) for t in toks if t.kind == STRING)
    # identifier chain like RequestMethod.POST or X.class
    if all((t.kind == IDENT) or (t.kind == OP and t.text == ".") for t in toks):
        return "".join(t.text for t in toks)
    return raw


def _decode_number(text: str) -> Any:
    s = text.replace("_", "")
    try:
        low = s.lower()
        if low.endswith("l"):
            return int(low[:-1], 0)
        if low.startswith("0x"):
            return int(low, 16)
        if low.startswith("0b"):
            return int(low[2:], 2)
        if low.endswith(("f", "d")) and not low.startswith("0x"):
            return float(low[:-1])
        if "." in low or "e" in low:
            return float(low)
        return int(low, 0) if not (len(low) > 1 and low.startswith("0") and low.isdigit()) else int(low, 8)
    except ValueError:
        return text


_JD_INLINE = re.compile(r"\{@(?:code|link|linkplain|literal|value)\s+([^}]*)\}")


def clean_comment(text: str) -> str:
    text = text.strip()
    if text.startswith("//"):
        return text[2:].strip()
    if text.startswith("/*"):
        body = text[2:]
        if body.startswith("*"):
            body = body[1:]
        if body.endswith("*/"):
            body = body[:-2]
        lines = [re.sub(r"^\s*\*\s?", "", l) for l in body.splitlines()]
        return " ".join(l.strip() for l in lines if l.strip()).strip()
    return text


def parse_javadoc(text: str) -> Javadoc:
    body = text
    if body.startswith("/**"):
        body = body[3:]
    if body.endswith("*/"):
        body = body[:-2]
    lines = [re.sub(r"^\s*\*\s?", "", l).rstrip() for l in body.splitlines()]
    desc: List[str] = []
    jd = Javadoc()
    cur_tag: Optional[str] = None
    cur_arg: Optional[str] = None
    cur_text: List[str] = []

    def flush() -> None:
        nonlocal cur_tag, cur_arg, cur_text
        if cur_tag is None:
            return
        txt = " ".join(x.strip() for x in cur_text if x.strip()).strip()
        if cur_tag == "param" and cur_arg:
            jd.params[cur_arg] = txt
        elif cur_tag == "return":
            jd.returns = txt
        elif cur_tag in ("throws", "exception") and cur_arg:
            jd.throws[cur_arg] = txt
        else:
            jd.tags[cur_tag] = (txt if not cur_arg else f"{cur_arg} {txt}").strip()
        cur_tag, cur_arg, cur_text = None, None, []

    for line in lines:
        m = re.match(r"^\s*@(\w+)\s*(.*)$", line)
        if m:
            flush()
            cur_tag = m.group(1)
            rest = m.group(2)
            if cur_tag in ("param", "throws", "exception"):
                parts = rest.split(None, 1)
                cur_arg = parts[0] if parts else ""
                cur_text = [parts[1]] if len(parts) > 1 else []
            else:
                cur_arg = None
                cur_text = [rest]
        elif cur_tag is not None:
            cur_text.append(line)
        else:
            desc.append(line)
    flush()
    text_desc = " ".join(l.strip() for l in desc if l.strip()).strip()
    jd.text = _JD_INLINE.sub(r"\1", text_desc)
    jd.params = {k: _JD_INLINE.sub(r"\1", v) for k, v in jd.params.items()}
    jd.returns = _JD_INLINE.sub(r"\1", jd.returns)
    return jd


def parse_java(src: str, path: str = "<memory>") -> JavaFile:
    return JavaParser(src, path).parse()
