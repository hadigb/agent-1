"""Extract visible business conditions from handler code and analyses."""
from __future__ import annotations

import re
from typing import List, Set

from ..detectors.common import VALIDATION_ANNOTATIONS
from ..detectors.model import EndpointSpec
from ..graph.index import CodeIndex
from ..graph.store import GraphStore
from ..javaparse.model import MethodDecl, TypeDecl

_IF_RE = re.compile(r"\bif\s*\(([^)]{3,240})\)")
_SKIP_IF = ("isDebugEnabled", "isTraceEnabled", "isInfoEnabled", "LOGGER.", "log.", "logger.")
_VALIDATE_CALLS = {
    "requireNonNull", "requireNonEmpty", "requireNotEmpty", "checkArgument", "checkState",
    "validate", "isTrue", "notNull", "assertTrue", "assertFalse", "hasText", "notBlank",
}


def collect_business_rules(
    spec: EndpointSpec,
    index: CodeIndex,
    store: GraphStore,
    analysis_notes: List[str],
    llm_rules: List[str],
) -> List[str]:
    rules: List[str] = []
    seen: Set[str] = set()

    def add(text: str) -> None:
        text = (text or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        rules.append(text)

    for note in llm_rules:
        add(note)
    for qname in _reachable_methods(store, spec):
        for rule in _rules_from_method(store, qname):
            add(rule)
    for type_decl in _related_types(spec, index):
        for rule in _rules_from_type(type_decl):
            add(rule)
    for note in analysis_notes:
        if any(token in note for token in ("اگر", "در صورتی", "باید", "نباید", "الزام")):
            add(note)
    return rules


def _reachable_methods(store: GraphStore, spec: EndpointSpec, depth: int = 4) -> List[str]:
    roots = [spec.handler_qname] + ([spec.impl_qname] if spec.impl_qname else [])
    seen: Set[str] = set()
    ordered: List[str] = []
    frontier = [(name, 0) for name in roots if name]
    while frontier:
        qname, current = frontier.pop(0)
        if not qname or qname in seen or qname.startswith("ext:"):
            continue
        seen.add(qname)
        ordered.append(qname)
        if current >= depth:
            continue
        for dest, _kind, _meta in store.edges_from(qname, "calls"):
            if dest.startswith("ext:") or dest in seen:
                continue
            frontier.append((dest, current + 1))
    return ordered


def _related_types(spec: EndpointSpec, index: CodeIndex) -> List[TypeDecl]:
    found: List[TypeDecl] = []
    seen: Set[str] = set()

    def add_qname(qname: str) -> None:
        if not qname or qname in seen:
            return
        type_decl = index.types.get(qname)
        if type_decl is None:
            return
        seen.add(qname)
        found.append(type_decl)

    add_qname(spec.type_qname)
    ctx = index.context_for(spec.type_qname) if spec.type_qname in index.types else None
    if spec.body_type is not None and ctx is not None:
        resolved = index.resolve_typeref(spec.body_type, ctx)
        if resolved.ok and resolved.qname:
            add_qname(resolved.qname)
    return found


def _rules_from_type(type_decl: TypeDecl) -> List[str]:
    out: List[str] = []
    for field in type_decl.fields:
        for annotation in field.annotations:
            name = annotation.simple_name
            if name not in VALIDATION_ANNOTATIONS:
                continue
            if name in ("NotNull", "NotBlank", "NotEmpty", "NonNull", "Nonnull", "Required"):
                out.append(f"فیلد {field.name} اجباری است.")
            elif name == "Size":
                lo, hi = annotation.get("min"), annotation.get("max")
                if lo is not None or hi is not None:
                    out.append(f"طول {field.name} باید بین {lo} و {hi} باشد.")
            elif name == "Pattern":
                regexp = annotation.get_str("regexp", "value")
                if regexp:
                    out.append(f"مقدار {field.name} باید با الگوی {regexp} مطابقت داشته باشد.")
            elif name in ("Min", "DecimalMin"):
                out.append(f"حداقل مقدار {field.name} برابر {annotation.get_str('value')} است.")
            elif name in ("Max", "DecimalMax"):
                out.append(f"حداکثر مقدار {field.name} برابر {annotation.get_str('value')} است.")
            elif name == "Email":
                out.append(f"فیلد {field.name} باید یک ایمیل معتبر باشد.")
            else:
                out.append(f"فیلد {field.name} با قاعده {name} اعتبارسنجی می‌شود.")
    return out


def _rules_from_method(store: GraphStore, qname: str) -> List[str]:
    symbol = store.get_symbol(qname)
    if symbol is None or symbol.kind != "method":
        return []
    method = MethodDecl.from_dict(symbol.data)
    out: List[str] = []
    if method.body is not None:
        for thrown in method.body.throws:
            name = thrown.type_name or "Exception"
            refs = ", ".join(thrown.refs) if thrown.refs else ""
            if refs:
                out.append(f"در شرایط خطا، {name} با {refs} پرتاب می‌شود.")
            else:
                out.append(f"مسیر خطا با {name} اعلام می‌شود.")
        for call in method.body.calls:
            if call.name not in _VALIDATE_CALLS:
                continue
            detail = ", ".join(call.string_args) if call.string_args else call.name
            out.append(f"اعتبارسنجی {detail} در متد {method.name} انجام می‌شود.")
    source = method.source or ""
    for match in _IF_RE.finditer(source):
        cond = re.sub(r"\s+", " ", match.group(1)).strip()
        if not cond or any(token in cond for token in _SKIP_IF):
            continue
        out.append(f"اگر {cond} آنگاه مسیر جایگزین اجرا می‌شود.")
    return out
