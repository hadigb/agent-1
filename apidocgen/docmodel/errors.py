"""Error-code catalog extraction and per-endpoint error collection."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from ..graph.index import CodeIndex
from ..graph.store import GraphStore
from .model import ErrorRow


class ErrorCatalog:
    """Maps constant qnames (Enum#CONST or Class#FIELD) to (code, message)."""

    def __init__(self, index: CodeIndex, class_regex: str) -> None:
        self.index = index
        self.entries: Dict[str, Tuple[str, str]] = {}
        self.catalog_types: List[str] = []
        pat = re.compile(class_regex) if class_regex else None
        for qname, t in index.types.items():
            if pat and not pat.match(t.name) and not pat.match(qname):
                continue
            if t.kind == "enum" and t.enum_constants:
                self.catalog_types.append(qname)
                for c in t.enum_constants:
                    code, msg = self._parse_args(c.args)
                    if code is None and not c.args:
                        code = c.name
                    if not msg:
                        msg = (c.javadoc.text if c.javadoc else "") or c.comment
                    self.entries[f"{qname}#{c.name}"] = (code or c.name, msg)
            elif t.kind in ("class", "interface"):
                consts = [f for f in t.fields if f.is_static and "final" in f.modifiers and f.initializer]
                if consts:
                    added = False
                    for f in consts:
                        init = f.initializer.strip()
                        if re.fullmatch(r"-?\d+[lL]?", init) or (init.startswith('"') and init.endswith('"')):
                            code = init.strip('"').rstrip("lL")
                            msg = (f.javadoc.text if f.javadoc else "") or f.comment
                            self.entries[f"{qname}#{f.name}"] = (code, msg)
                            added = True
                    if added:
                        self.catalog_types.append(qname)

    @staticmethod
    def _parse_args(args: List[str]) -> Tuple[Optional[str], str]:
        code: Optional[str] = None
        msg = ""
        for a in args:
            a = a.strip()
            if re.fullmatch(r"-?\d+[lL]?", a) and code is None:
                code = a.rstrip("lL")
            elif a.startswith('"') and a.endswith('"'):
                inner = a[1:-1]
                if re.fullmatch(r"-?\d+", inner) and code is None:
                    code = inner
                elif not msg:
                    msg = inner
        return code, msg

    def lookup(self, const_qname: str) -> Optional[Tuple[str, str]]:
        return self.entries.get(const_qname)


def collect_endpoint_errors(store: GraphStore, catalog: ErrorCatalog, handler_qnames: List[str],
                            depth: int = 3) -> List[ErrorRow]:
    """Follow ``calls`` edges from the handler(s) and gather catalog constants used along the way."""
    seen_methods: Set[str] = set()
    frontier: List[Tuple[str, int]] = [(h, 0) for h in handler_qnames if h]
    found: List[ErrorRow] = []
    seen_codes: Set[str] = set()
    while frontier:
        mq, d = frontier.pop(0)
        if mq in seen_methods:
            continue
        seen_methods.add(mq)
        for dst, kind, meta in store.edges_from(mq):
            if kind == "uses_const":
                hit = catalog.lookup(dst)
                if hit and hit[0] not in seen_codes:
                    seen_codes.add(hit[0])
                    found.append(ErrorRow(code=hit[0], title=hit[1], source="code"))
            elif kind == "calls" and d < depth and not dst.startswith("ext:"):
                frontier.append((dst, d + 1))
    return found


def _sort_key(code: str):
    try:
        return (0, int(code))
    except ValueError:
        return (1, code)


def merge_error_rows(*groups: List[ErrorRow], success_first: Optional[Dict] = None,
                     always_last: Optional[List[Dict]] = None) -> List[ErrorRow]:
    """Merge rows by code keeping the first title seen; success code first, config codes last."""
    out: List[ErrorRow] = []
    seen: Dict[str, ErrorRow] = {}
    if success_first:
        r = ErrorRow(code=str(success_first.get("code", 1)), title=success_first.get("title", ""), source="config")
        out.append(r)
        seen[r.code] = r
    for g in groups:
        for r in g:
            code = str(r.code)
            if code in seen:
                existing = seen[code]
                if not existing.title and r.title:
                    existing.title = r.title
                if not existing.note and r.note:
                    existing.note = r.note
                continue
            r.code = code
            seen[code] = r
            out.append(r)
    for c in always_last or []:
        code = str(c.get("code"))
        if code in seen:
            continue
        r = ErrorRow(code=code, title=c.get("title", ""), note=c.get("note", ""), source="config")
        seen[code] = r
        out.append(r)
    return out
