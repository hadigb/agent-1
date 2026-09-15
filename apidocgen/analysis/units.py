"""Analysis units: the smallest pieces of code that are sent to an LLM.

Two kinds exist:

* ``type`` units - one DTO / enum class.  Shared between endpoints, cached by
  the hash of the class' documentation-relevant source (fields, annotations,
  comments, enum constants, inherited fields) so a DTO used by ten endpoints
  is described exactly once, and a change in an unrelated method body never
  invalidates it.
* ``endpoint`` units - one handler method plus the *skeletons* (names/types
  only) of the types it exchanges and the bodies of the project methods it
  calls (depth limited, token budgeted).

The unit hash is the SHA-256 of the whitespace-normalised payload, so the
cache key depends purely on the code content that the model would see.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..config import Config
from ..detectors.model import EndpointSpec
from ..docmodel.errors import ErrorCatalog
from ..docmodel.model import EndpointDoc
from ..graph.index import CodeIndex
from ..graph.store import GraphStore
from ..javaparse.model import FieldDecl, MethodDecl, TypeDecl
from ..util.tokens import estimate_tokens, truncate_to_tokens

PROMPT_VERSION = "2"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def cache_key(kind: str, unit_hash: str, prompt_version: str, model: Optional[str]) -> str:
    raw = f"{prompt_version}|{kind}|{unit_hash}|{model or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class TypeUnit:
    kind: str
    unit_id: str            # type qname
    name: str
    hash: str
    payload: str
    field_names: List[str] = field(default_factory=list)
    enum_names: List[str] = field(default_factory=list)
    tokens: int = 0
    used_by: List[str] = field(default_factory=list)   # endpoint ids


@dataclass
class EndpointUnit:
    kind: str
    unit_id: str            # endpoint id
    name: str
    hash: str
    payload: str
    param_names: List[str] = field(default_factory=list)
    response_names: List[str] = field(default_factory=list)
    tokens: int = 0
    type_names: List[str] = field(default_factory=list)


@dataclass
class IntroUnit:
    kind: str
    unit_id: str
    name: str
    hash: str
    payload: str
    tokens: int = 0


_ACCESSOR = re.compile(r"^(get|set|is|has|with|to|builder|equals|hashCode|toString)[A-Z_]?")


class UnitBuilder:
    def __init__(self, cfg: Config, index: CodeIndex, store: GraphStore) -> None:
        self.cfg = cfg
        self.index = index
        self.store = store
        self.catalog = ErrorCatalog(index, cfg.get("analysis", "error_catalog_regex", default="") or "")
        self.call_depth = int(cfg.get("analysis", "call_depth", default=1))
        self.include_bodies = bool(cfg.get("analysis", "include_callee_bodies", default=True))
        self.max_slice = int(cfg.get("analysis", "max_slice_tokens", default=6000))
        self.max_type = int(cfg.get("analysis", "max_type_tokens", default=2500))

    # ------------------------------------------------------------------ type units
    def _rel(self, path: str) -> str:
        """Path relative to the config root (keeps cache keys stable when the checkout moves)."""
        try:
            return os.path.relpath(path, str(self.cfg.root)).replace(os.sep, "/")
        except ValueError:
            return os.path.basename(path)

    def type_slice(self, qname: str) -> Tuple[str, List[str], List[str], str]:
        """Return (payload, field names present in the payload, enum constants, full untruncated text)."""
        t = self.index.types[qname]
        lines: List[str] = []
        jf = self.index.type_file[qname]
        lines.append(f"// file: {self._rel(jf.path)}")
        header = t.header_source or f"{t.kind} {t.name}"
        lines.append(header + " {")
        field_names: List[str] = []
        enum_names: List[str] = []
        if t.kind == "enum":
            for c in t.enum_constants:
                doc = self._doc_prefix(c.javadoc.text if c.javadoc else "", c.comment)
                args = f"({', '.join(c.args)})" if c.args else ""
                lines.append(f"    {doc}{c.name}{args},")
                enum_names.append(c.name)
            for f in t.fields:
                if not f.is_static:
                    lines.append("    " + normalize(f.source))
        if t.kind == "record":
            for p in t.record_components:
                annos = " ".join("@" + a.name + (a.raw if a.raw else "") for a in p.annotations)
                lines.append(f"    {annos} {p.type.canonical()} {p.name};".replace("  ", " "))
                field_names.append(p.name)
        # own + inherited instance fields (base first, like the document tables); stop adding fields once the
        # token budget is used up so the model is only asked about fields it can actually see
        used = estimate_tokens("\n".join(lines))
        full_lines = list(lines)
        omitted = 0
        for f, owner in self.index.all_fields(qname):
            entry = ("    // inherited from " + owner.rsplit(".", 1)[-1] + "\n" if owner != qname else "") + "    " + self._field_source(f)
            full_lines.append(entry)
            cost = estimate_tokens(entry)
            if used + cost > self.max_type and field_names:
                omitted += 1
                continue
            used += cost
            lines.append(entry)
            if f.name not in field_names:
                field_names.append(f.name)
        if omitted:
            lines.append(f"    // ... {omitted} more fields omitted (analysis.max_type_tokens)")
        # nested enums / classes declared inside this type are separate units; mention them
        for n in t.nested:
            lines.append(f"    // nested {n.kind} {n.name}")
            full_lines.append(f"    // nested {n.kind} {n.name}")
        lines.append("}")
        full_lines.append("}")
        return "\n".join(lines), field_names, enum_names, "\n".join(full_lines)

    @staticmethod
    def _doc_prefix(javadoc: str, comment: str) -> str:
        d = (javadoc or comment or "").strip()
        return f"/** {d} */ " if d else ""

    @staticmethod
    def _field_source(f: FieldDecl) -> str:
        src = f.source.strip() if f.source else f"{f.type.canonical()} {f.name};"
        src = normalize(src)
        doc = (f.javadoc.text if f.javadoc else "") or f.comment
        if doc and doc not in src:
            return f"/** {doc} */ {src}"
        return src

    def type_unit(self, qname: str) -> TypeUnit:
        payload, fields, enums, full_text = self.type_slice(qname)
        t = self.index.types[qname]
        # the hash covers the *untruncated* class so that any change (even past the cut) invalidates the analysis
        return TypeUnit(kind="type", unit_id=qname, name=t.name, hash=content_hash(full_text), payload=payload,
                        field_names=fields, enum_names=enums, tokens=estimate_tokens(payload))

    # ------------------------------------------------------------------ intro unit
    def intro_unit(self, specs: List[EndpointSpec], docs: Dict[str, EndpointDoc]) -> IntroUnit:
        system = self.cfg.get("doc", "system_name", default="") or ""
        lines = [f"// system: {system}", "// services (method path - summary):"]
        for s in specs:
            d = docs.get(s.id)
            summary = (d.description if d else s.summary or s.description)[:300]
            lines.append(f"{s.http_method} {s.path} - {summary}")
        payload = "\n".join(lines)
        return IntroUnit(kind="intro", unit_id="__intro__", name=system or "intro", hash=content_hash(payload),
                         payload=payload, tokens=estimate_tokens(payload))

    # ------------------------------------------------------------------ endpoint units
    def type_skeleton(self, qname: str) -> str:
        t = self.index.types[qname]
        if t.kind == "enum":
            return f"enum {t.name} {{ {', '.join(c.name for c in t.enum_constants)} }}"
        parts = [f"{f.type.canonical()} {f.name}" for f, _o in self.index.all_fields(qname)]
        return f"{t.kind} {t.name} {{ {'; '.join(parts)} }}"

    def _method_text(self, mq: str, with_body: bool) -> Optional[str]:
        sym = self.store.get_symbol(mq)
        if sym is None or sym.kind != "method":
            return None
        m = MethodDecl.from_dict(sym.data)
        owner = sym.parent or ""
        if with_body and m.source:
            return f"// {owner}\n{m.source.strip()}"
        return f"// {owner}\n{(m.signature_source or m.source).strip()};"

    def endpoint_unit(self, spec: EndpointSpec, doc: EndpointDoc) -> EndpointUnit:
        budget = self.max_slice
        parts: List[str] = []
        # 1) controller / handler class header + handler method (+ implementation)
        t = self.index.types.get(spec.type_qname)
        if t is not None:
            jf = self.index.type_file[spec.type_qname]
            parts.append(f"// file: {self._rel(jf.path)}\n{normalize(t.header_source)} {{")
        handler_text = self._method_text(spec.handler_qname, with_body=True) or ""
        parts.append(handler_text)
        if spec.impl_qname:
            impl_text = self._method_text(spec.impl_qname, with_body=True)
            if impl_text:
                parts.append("// implementation\n" + impl_text)
        if t is not None:
            parts.append("}")
        core = "\n\n".join(parts)
        remaining = budget - estimate_tokens(core)
        # 2) type skeletons
        skel_lines = ["// types exchanged by this endpoint (names/types only; documented separately)"]
        for q in doc.type_closure:
            if q in self.index.types:
                skel_lines.append(self.type_skeleton(q))
        skel = "\n".join(skel_lines)
        skel = truncate_to_tokens(skel, max(300, int(remaining * 0.35)))
        remaining -= estimate_tokens(skel)
        # 3) callees (breadth first, depth limited)
        callee_lines: List[str] = []
        seen: Set[str] = {spec.handler_qname}
        if spec.impl_qname:
            seen.add(spec.impl_qname)
        frontier: List[Tuple[str, int]] = [(spec.handler_qname, 0)]
        if spec.impl_qname:
            frontier.append((spec.impl_qname, 0))
        while frontier and remaining > 200:
            mq, d = frontier.pop(0)
            if d >= self.call_depth:
                continue
            for dst, kind, meta in self.store.edges_from(mq, "calls"):
                if dst.startswith("ext:") or dst in seen:
                    continue
                seen.add(dst)
                mname = dst.split("#", 1)[1].split("(", 1)[0] if "#" in dst else dst
                owner_q = dst.split("#", 1)[0]
                owner_t = self.index.types.get(owner_q)
                # skip trivial accessors and DTO methods
                if _ACCESSOR.match(mname) and owner_t is not None and owner_q in doc.type_closure:
                    continue
                text = self._method_text(dst, with_body=self.include_bodies)
                if not text:
                    continue
                if _ACCESSOR.match(mname) and len(text) < 160:
                    continue
                cost = estimate_tokens(text)
                if cost > remaining:
                    text = truncate_to_tokens(text, max(100, remaining))
                    cost = estimate_tokens(text)
                callee_lines.append(text)
                remaining -= cost
                frontier.append((dst, d + 1))
                if remaining <= 200:
                    break
        # 4) error catalog constants referenced anywhere in the slice
        catalog_lines: List[str] = []
        for row in doc.error_rows:
            if row.source == "code":
                catalog_lines.append(f"{row.code}: {row.title}")
        payload_parts = [core, skel]
        if callee_lines:
            payload_parts.append("// called project methods\n" + "\n\n".join(callee_lines))
        if catalog_lines:
            payload_parts.append("// error codes referenced by this endpoint (code: message)\n" + "\n".join(catalog_lines))
        payload = "\n\n".join(payload_parts)
        payload = truncate_to_tokens(payload, budget)
        param_names = [r.name for r in doc.header_rows + doc.request_rows]
        response_names = [r.name for r in doc.response_rows]
        for table in doc.nested_tables:
            names = param_names if table.side == "request" else response_names
            names.extend(f"{table.qname}.{r.name}" for r in table.fields)
        param_names = list(dict.fromkeys(param_names))
        response_names = list(dict.fromkeys(response_names))
        return EndpointUnit(kind="endpoint", unit_id=spec.id, name=spec.id, hash=content_hash(payload), payload=payload,
                            param_names=param_names, response_names=response_names, tokens=estimate_tokens(payload),
                            type_names=[q.rsplit(".", 1)[-1] for q in doc.type_closure])
