"""Resolve annotation path expressions such as ``Routes.BASE + "/x"``.

Values are read from source. Local constants, constants on other project types,
static imports and ``+`` concatenation of string literals are expanded.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, TYPE_CHECKING

from ..javaparse.model import TypeDecl
from ..javaparse.tokenizer import unquote_string

if TYPE_CHECKING:
    from ..graph.index import CodeIndex

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUALIFIED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def split_plus_expr(expr: str) -> list[str]:
    """Split a Java ``+`` concatenation, ignoring plus signs inside string literals."""
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    in_str = False
    while i < len(expr):
        c = expr[i]
        if in_str:
            buf.append(c)
            if c == "\\" and i + 1 < len(expr):
                buf.append(expr[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            buf.append(c)
            i += 1
            continue
        if c == "+":
            piece = "".join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    piece = "".join(buf).strip()
    if piece:
        parts.append(piece)
    return parts


def literal_string(token: str) -> Optional[str]:
    token = token.strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return unquote_string(token)
    return None


class ConstantResolver:
    def __init__(self, owner: TypeDecl, index: Optional["CodeIndex"] = None) -> None:
        self.owner = owner
        self.index = index
        self._memo: Dict[str, Optional[str]] = {}
        self._stack: set[str] = set()

    def expand(self, raw: Any) -> Any:
        if isinstance(raw, list):
            return [self.expand(x) for x in raw]
        if not isinstance(raw, str):
            return raw
        value = self._expand_expr(raw.strip(), self.owner)
        return value if value is not None else raw

    def _expand_expr(self, expr: str, owner: TypeDecl, depth: int = 0) -> Optional[str]:
        if depth > 12:
            return None
        lit = literal_string(expr)
        if lit is not None:
            return lit
        parts = split_plus_expr(expr)
        if len(parts) > 1:
            out = []
            for part in parts:
                piece = self._expand_expr(part, owner, depth + 1)
                if piece is None:
                    return None
                out.append(piece)
            return "".join(out)
        return self._resolve_name(expr, owner, depth)

    def _resolve_name(self, name: str, owner: TypeDecl, depth: int) -> Optional[str]:
        name = name.strip()
        if not name:
            return None
        key = f"{owner.qname}::{name}"
        if key in self._memo:
            return self._memo[key]
        if key in self._stack:
            return None
        self._stack.add(key)
        try:
            value = self._lookup(name, owner, depth)
        finally:
            self._stack.discard(key)
        self._memo[key] = value
        return value

    def _lookup(self, name: str, owner: TypeDecl, depth: int) -> Optional[str]:
        if _IDENT.fullmatch(name):
            local = self._field_value(owner, name, depth)
            if local is not None:
                return local
            imported = self._static_import(name, depth)
            if imported is not None:
                return imported
            return None
        if _QUALIFIED.fullmatch(name):
            type_name, const = name.rsplit(".", 1)
            target = self._resolve_type(type_name, owner)
            if target is not None:
                return self._field_value(target, const, depth)
        return None

    def _field_value(self, t: TypeDecl, field_name: str, depth: int) -> Optional[str]:
        for f in t.fields:
            if f.name != field_name or not f.is_static or not f.initializer:
                continue
            return self._expand_expr(f.initializer.strip(), t, depth + 1)
        return None

    def _static_import(self, name: str, depth: int) -> Optional[str]:
        if self.index is None or self.owner.qname not in self.index.type_file:
            return None
        jf = self.index.type_file[self.owner.qname]
        for imp in jf.imports:
            if not imp.static:
                continue
            if imp.wildcard:
                owner_name = imp.name
                target = self.index.types.get(owner_name)
                if target:
                    value = self._field_value(target, name, depth)
                    if value is not None:
                        return value
                continue
            if imp.name.rsplit(".", 1)[-1] != name:
                continue
            owner_name = imp.name.rsplit(".", 1)[0]
            target = self.index.types.get(owner_name)
            if target:
                return self._field_value(target, name, depth)
        return None

    def _resolve_type(self, type_name: str, owner: TypeDecl) -> Optional[TypeDecl]:
        if self.index is None:
            return None
        if type_name in self.index.types:
            return self.index.types[type_name]
        if owner.qname not in self.index.types:
            return None
        ctx = self.index.context_for(owner.qname)
        res = self.index.resolve(type_name, ctx)
        if res.ok and res.qname in self.index.types:
            return self.index.types[res.qname]
        cands = self.index.simple.get(type_name.rsplit(".", 1)[-1], [])
        if len(cands) == 1:
            return self.index.types.get(cands[0])
        return None


def expand_constants(raw: Any, t: TypeDecl, index: Optional["CodeIndex"] = None) -> Any:
    """Turn ``BASE + "/x"`` / ``Routes.FOO`` annotation values into concrete strings."""
    return ConstantResolver(t, index).expand(raw)
