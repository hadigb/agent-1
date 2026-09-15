"""A small, robust Java tokenizer.

It produces a flat list of tokens with character offsets and line numbers.
Comments are kept as tokens (the parser uses them for javadoc / inline
descriptions) but are skipped transparently by the parser's cursor helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

IDENT = "ident"
NUMBER = "number"
STRING = "string"
CHAR = "char"
OP = "op"
COMMENT = "comment"      # /* ... */ and // ...
JAVADOC = "javadoc"      # /** ... */
EOF = "eof"

KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const",
    "continue", "default", "do", "double", "else", "enum", "extends", "final", "finally", "float",
    "for", "goto", "if", "implements", "import", "instanceof", "int", "interface", "long", "native",
    "new", "package", "private", "protected", "public", "return", "short", "static", "strictfp",
    "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try", "void",
    "volatile", "while", "true", "false", "null",
}

MODIFIERS = {
    "public", "protected", "private", "static", "final", "abstract", "transient", "volatile",
    "synchronized", "native", "strictfp", "default", "sealed", "non-sealed",
}

PRIMITIVES = {"boolean", "byte", "char", "short", "int", "long", "float", "double", "void"}

# multi-character operators we care to keep together
_MULTI_OPS = ("->", "::", "...", "==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=", "*=",
              "/=", "%=", "&=", "|=", "^=")


@dataclass
class Token:
    kind: str
    text: str
    start: int   # char offset
    end: int
    line: int

    def __repr__(self) -> str:  # pragma: no cover
        return f"Token({self.kind}, {self.text!r}, L{self.line})"


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_" or ch == "$" or (ord(ch) > 127 and ch.isidentifier())


def _is_ident_part(ch: str) -> bool:
    return ch.isalnum() or ch == "_" or ch == "$" or (ord(ch) > 127 and ("x" + ch).isidentifier())


def tokenize(src: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0
    n = len(src)
    line = 1
    while i < n:
        ch = src[i]
        # whitespace
        if ch in " \t\r\f\v":
            i += 1
            continue
        if ch == "\n":
            line += 1
            i += 1
            continue
        start = i
        start_line = line
        # comments
        if ch == "/" and i + 1 < n:
            nxt = src[i + 1]
            if nxt == "/":
                j = src.find("\n", i)
                if j == -1:
                    j = n
                tokens.append(Token(COMMENT, src[i:j], i, j, start_line))
                i = j
                continue
            if nxt == "*":
                j = src.find("*/", i + 2)
                if j == -1:
                    j = n
                else:
                    j += 2
                text = src[i:j]
                kind = JAVADOC if text.startswith("/**") and not text.startswith("/**/") else COMMENT
                tokens.append(Token(kind, text, i, j, start_line))
                line += text.count("\n")
                i = j
                continue
        # text block """..."""
        if ch == '"' and src.startswith('"""', i):
            j = src.find('"""', i + 3)
            while j != -1 and src[j - 1] == "\\":
                j = src.find('"""', j + 1)
            if j == -1:
                j = n
            else:
                j += 3
            text = src[i:j]
            tokens.append(Token(STRING, text, i, j, start_line))
            line += text.count("\n")
            i = j
            continue
        # string literal
        if ch == '"':
            j = i + 1
            while j < n:
                c = src[j]
                if c == "\\":
                    j += 2
                    continue
                if c == '"' or c == "\n":
                    break
                j += 1
            j = min(j + 1, n)
            tokens.append(Token(STRING, src[i:j], i, j, start_line))
            i = j
            continue
        # char literal
        if ch == "'":
            j = i + 1
            while j < n:
                c = src[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "'" or c == "\n":
                    break
                j += 1
            j = min(j + 1, n)
            tokens.append(Token(CHAR, src[i:j], i, j, start_line))
            i = j
            continue
        # identifiers / keywords
        if _is_ident_start(ch):
            j = i + 1
            while j < n and _is_ident_part(src[j]):
                j += 1
            # handle the "non-sealed" contextual keyword
            if src[i:j] == "non" and src.startswith("-sealed", j):
                j += len("-sealed")
            tokens.append(Token(IDENT, src[i:j], i, j, start_line))
            i = j
            continue
        # numbers (including hex, binary, underscores, suffixes, floats)
        if ch.isdigit() or (ch == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i + 1
            if ch == "0" and j < n and src[j] in "xXbB":
                j += 1
            while j < n and (src[j].isalnum() or src[j] in "._"):
                # stop at '..' (should not happen) but allow floats/exponents
                if src[j] in "eE" and j + 1 < n and src[j + 1] in "+-" and src[i:j].lower().count("x") == 0:
                    j += 2
                    continue
                j += 1
            tokens.append(Token(NUMBER, src[i:j], i, j, start_line))
            i = j
            continue
        # operators / punctuation
        matched = None
        for op in _MULTI_OPS:
            if src.startswith(op, i):
                matched = op
                break
        if matched is None:
            matched = ch
        tokens.append(Token(OP, matched, i, i + len(matched), start_line))
        i += len(matched)
    tokens.append(Token(EOF, "", n, n, line))
    return tokens


def unquote_string(text: str) -> str:
    """Decode a Java string literal token (regular or text block) to its value."""
    if text.startswith('"""'):
        body = text[3:-3] if text.endswith('"""') else text[3:]
        # strip the first line break after the opening delimiter
        if body.startswith("\n"):
            body = body[1:]
        elif body.startswith("\r\n"):
            body = body[2:]
        lines = body.split("\n")
        # remove common indentation (incidental whitespace)
        indents = [len(l) - len(l.lstrip(" \t")) for l in lines if l.strip()]
        common = min(indents) if indents else 0
        body = "\n".join(l[common:] if len(l) >= common else l.lstrip() for l in lines)
        return _decode_escapes(body)
    if text.startswith('"'):
        inner = text[1:-1] if text.endswith('"') and len(text) >= 2 else text[1:]
        return _decode_escapes(inner)
    return text


def _decode_escapes(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    simple = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "s": " ", "0": "\0", "'": "'", '"': '"', "\\": "\\"}
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            d = s[i + 1]
            if d == "u":
                j = i + 2
                while j < n and s[j] == "u":
                    j += 1
                hexpart = s[j:j + 4]
                try:
                    out.append(chr(int(hexpart, 16)))
                    i = j + 4
                    continue
                except ValueError:
                    pass
            if d in simple:
                out.append(simple[d])
                i += 2
                continue
            if d == "\n":  # line continuation in text blocks
                i += 2
                continue
            out.append(d)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)
