"""Field expansion: turns Java DTO types into document rows (wire names, doc
types, required flags, nested/enum links) and collects the type closure."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from ..detectors.common import (
    description_from_annotations, is_ignored_field, required_from_annotations, wire_name,
)
from ..graph.index import CodeIndex, ResolveContext
from ..javaparse.model import Annotation, FieldDecl, TypeDecl, TypeRef, find_annotation
from .model import EnumTable, EnumValue, FieldRow, TypeTable

# Java type -> document type
_DOC_TYPES = {
    "String": "String", "CharSequence": "String", "char": "String", "Character": "String", "UUID": "String",
    "int": "Int", "Integer": "Int", "short": "Int", "Short": "Int", "byte": "Int", "Byte": "Int",
    "long": "Long", "Long": "Long", "BigInteger": "BigInteger",
    "boolean": "Boolean", "Boolean": "Boolean",
    "double": "Double", "Double": "Double", "float": "Double", "Float": "Double", "Number": "Double",
    "BigDecimal": "BigDecimal",
    "LocalDate": "String", "LocalDateTime": "String", "LocalTime": "String", "Instant": "String", "Date": "String",
    "ZonedDateTime": "String", "OffsetDateTime": "String", "Timestamp": "String", "Calendar": "String",
    "Object": "Object", "JsonNode": "Object", "Map": "Map", "HashMap": "Map", "LinkedHashMap": "Map", "TreeMap": "Map",
    "byte[]": "String (Base64)", "MultipartFile": "File", "InputStream": "File", "Resource": "File",
    "Void": "-", "void": "-",
}
_COLLECTIONS = {"List", "Set", "Collection", "ArrayList", "LinkedList", "HashSet", "LinkedHashSet", "Iterable",
                "SortedSet", "TreeSet", "Flux", "Multi", "Stream"}
_DATE_TYPES = {"LocalDate", "LocalDateTime", "LocalTime", "Instant", "Date", "ZonedDateTime", "OffsetDateTime",
               "Timestamp", "Calendar"}


def naming_strategy(t: TypeDecl, index: CodeIndex) -> Optional[str]:
    """Jackson @JsonNaming strategy declared on the type or an ancestor."""
    for owner in index.superclass_chain(t.qname):
        ot = index.types.get(owner)
        if not ot:
            continue
        a = find_annotation(ot.annotations, "JsonNaming")
        if a:
            v = str(a.get_str("value") or "")
            for key in ("UpperCamelCase", "SnakeCase", "LowerCase", "KebabCase", "LowerDotCase", "UpperSnakeCase",
                        "LowerCamelCase"):
                if key in v:
                    return key
            return None
    return None


def apply_naming(name: str, strategy: Optional[str]) -> str:
    if not strategy or strategy == "LowerCamelCase":
        return name
    if strategy == "UpperCamelCase":
        return name[:1].upper() + name[1:]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    if strategy == "SnakeCase":
        return words
    if strategy == "UpperSnakeCase":
        return words.upper()
    if strategy == "KebabCase":
        return words.replace("_", "-")
    if strategy == "LowerDotCase":
        return words.replace("_", ".")
    if strategy == "LowerCase":
        return name.lower()
    return name


def substitute(ref: TypeRef, bindings: Dict[str, TypeRef], _depth: int = 0) -> TypeRef:
    """Replace type variables (T -> IssueResult) recursively.

    A binding's own arguments are substituted with the *remaining* bindings only, so a self-referential
    binding such as ``{T: List<T>}`` (generic base controllers) cannot recurse forever.
    """
    if _depth > 12:
        return ref
    if ref.name in bindings and not ref.args:
        b = bindings[ref.name]
        inner = {k: v for k, v in bindings.items() if k != ref.name}
        return TypeRef(name=b.name, args=[substitute(a, inner, _depth + 1) for a in b.args], dims=b.dims + ref.dims,
                       wildcard=b.wildcard, annotations=ref.annotations)
    return TypeRef(name=ref.name, args=[substitute(a, bindings, _depth + 1) for a in ref.args], dims=ref.dims,
                   wildcard=ref.wildcard, annotations=ref.annotations)


class FieldExpander:
    def __init__(self, index: CodeIndex) -> None:
        self.index = index

    # ------------------------------------------------------------------ type strings
    def classify(self, ref: TypeRef, ctx: ResolveContext) -> Tuple[str, Optional[str], Optional[str], bool]:
        """Return (doc type string, nested project type qname, enum qname, is_list)."""
        ref = self._unwrap_optional(ref)
        if ref.wildcard:
            inner = ref.args[0] if ref.args else TypeRef(name="Object")
            return self.classify(inner, ctx)
        if ref.dims > 0:
            if ref.name == "byte" and ref.dims == 1:
                return "String (Base64)", None, None, False
            inner = TypeRef(name=ref.name, args=ref.args, dims=ref.dims - 1)
            s, nested, enum, _ = self.classify(inner, ctx)
            return f"List<{s}>", nested, enum, True
        n = ref.simple_name
        if n in _COLLECTIONS:
            inner = ref.args[0] if ref.args else TypeRef(name="Object")
            s, nested, enum, _ = self.classify(inner, ctx)
            return f"List<{s}>", nested, enum, True
        if n in ("Map", "HashMap", "LinkedHashMap", "TreeMap", "SortedMap", "MultiValueMap"):
            if len(ref.args) == 2:
                k, _, _, _ = self.classify(ref.args[0], ctx)
                v, nested, enum, _ = self.classify(ref.args[1], ctx)
                return f"Map<{k}, {v}>", nested, enum, False
            return "Map", None, None, False
        res = self.index.resolve(ref.name, ctx)
        if res.ok:
            t = self.index.types[res.qname]
            if t.kind == "enum":
                return "String", None, res.qname, False
            if t.kind in ("class", "record", "interface"):
                return t.name, res.qname, None, False
        if n in _DOC_TYPES:
            return _DOC_TYPES[n], None, None, False
        if res.kind == "typevar":
            return "Object", None, None, False
        return n, None, None, False

    @staticmethod
    def _unwrap_optional(ref: TypeRef) -> TypeRef:
        if ref.simple_name in ("Optional", "Mono", "Uni", "Single", "Maybe", "CompletableFuture") and ref.args:
            return ref.args[0]
        return ref

    # ------------------------------------------------------------------ rows
    def field_description(self, f: FieldDecl) -> str:
        d = description_from_annotations(f.annotations)
        if d:
            return d
        if f.javadoc and f.javadoc.text:
            return f.javadoc.text
        return f.comment or ""

    def rows_for_type(self, qname: str, bindings: Optional[Dict[str, TypeRef]] = None, location: str = "body") -> List[FieldRow]:
        t = self.index.types[qname]
        strategy = naming_strategy(t, self.index)
        rows: List[FieldRow] = []
        record_doc = t.javadoc.params if (t.kind == "record" and t.javadoc) else {}
        for f, owner in self.index.all_fields(qname):
            if is_ignored_field(f.annotations):
                continue
            if "transient" in f.modifiers:
                continue
            ftype = substitute(f.type, bindings or {})
            owner_ctx = self.index.context_for(owner)
            type_str, nested, enum, is_list = self.classify(ftype, owner_ctx)
            req = required_from_annotations(f.annotations, ftype)
            desc = self.field_description(f) or record_doc.get(f.name, "")
            if ftype.simple_name in _DATE_TYPES and "yyyy" not in desc and "فرمت" not in desc:
                desc = (desc + " (تاریخ)").strip()
            name = apply_naming(wire_name(f.annotations, f.name), strategy) if not any(
                a.simple_name in ("JsonProperty", "SerializedName") for a in f.annotations) else wire_name(f.annotations, f.name)
            rows.append(FieldRow(name=name, java_name=f.name, type_str=type_str, required=bool(req),
                                 code_description=desc, description=desc, location=location, enum_qname=enum,
                                 nested_qname=nested, is_list=is_list, owner_qname=owner, java_type=ftype.canonical()))
        return rows

    def enum_table(self, qname: str) -> EnumTable:
        t = self.index.types[qname]
        et = EnumTable(qname=qname, name=t.name, title=(t.javadoc.text if t.javadoc else t.comment) or t.name)
        for c in t.enum_constants:
            label = ""
            code: Optional[str] = None
            for a in c.args:
                a = a.strip()
                if a.startswith('"') and a.endswith('"') and not label:
                    label = a[1:-1]
                elif re.fullmatch(r"-?\d+[lL]?", a) and code is None:
                    code = a.rstrip("lL")
            if not label:
                label = (c.javadoc.text if c.javadoc else "") or c.comment
            et.values.append(EnumValue(name=c.name, label=label, code=code))
        return et

    # ------------------------------------------------------------------ closure
    def expand(self, root: Optional[TypeRef], ctx: ResolveContext, side: str,
               visited: Set[str], tables: List[TypeTable], enums: Dict[str, EnumTable],
               flatten_root: bool = True) -> Tuple[List[FieldRow], Optional[str]]:
        """Expand ``root`` into rows; nested project types become tables (depth-first, order of appearance).

        Returns (rows for the root type, root type qname or None).
        """
        if root is None:
            return [], None
        root = self._unwrap_optional(root)
        type_str, nested, enum, is_list = self.classify(root, ctx)
        if enum and enum not in enums:
            enums[enum] = self.enum_table(enum)
        if not nested:
            # scalar / list of scalars / map at the root
            row = FieldRow(name="body" if side == "request" else "result", java_name="", type_str=type_str,
                           required=True, description="", location="body" if side == "request" else "response",
                           enum_qname=enum, is_list=is_list)
            return [row], None
        if is_list and nested:
            bindings = self._bindings_for(root, nested)
            if nested not in visited:
                visited.add(nested)
                t = self.index.types[nested]
                sub_rows = self.rows_for_type(nested, bindings, location="body" if side == "request" else "response")
                tables.append(TypeTable(qname=nested, name=t.name, title=t.name,
                                        description=(t.javadoc.text if t.javadoc else t.comment) or "",
                                        fields=sub_rows, side=side))
                self._collect_nested(sub_rows, nested, bindings, side, visited, tables, enums)
            row = FieldRow(name="body" if side == "request" else "result", java_name="", type_str=type_str,
                           required=True, description="", location="body" if side == "request" else "response",
                           nested_qname=nested, is_list=True)
            return [row], nested
        bindings = self._bindings_for(root, nested)
        rows = self.rows_for_type(nested, bindings, location="body" if side == "request" else "response")
        visited.add(nested)
        self._collect_nested(rows, nested, bindings, side, visited, tables, enums)
        return rows, nested

    def _bindings_for(self, ref: TypeRef, qname: str) -> Dict[str, TypeRef]:
        t = self.index.types[qname]
        inner = ref
        # List<X> -> X
        inner = self._unwrap_optional(inner)
        if inner.simple_name in _COLLECTIONS and inner.args:
            inner = inner.args[0]
        if inner.dims:
            inner = TypeRef(name=inner.name, args=inner.args)
        b: Dict[str, TypeRef] = {}
        for tp, arg in zip(t.type_params, inner.args):
            b[tp] = arg
        return b

    def _collect_nested(self, rows: List[FieldRow], owner: str, bindings: Dict[str, TypeRef], side: str,
                        visited: Set[str], tables: List[TypeTable], enums: Dict[str, EnumTable]) -> None:
        for r in rows:
            if r.enum_qname and r.enum_qname not in enums:
                enums[r.enum_qname] = self.enum_table(r.enum_qname)
            if r.nested_qname and r.nested_qname not in visited:
                visited.add(r.nested_qname)
                t = self.index.types[r.nested_qname]
                # bindings for the nested generic type, e.g. Page<Item> field
                f = next((ff for ff, _o in self.index.all_fields(owner) if ff.name == r.java_name), None)
                nb: Dict[str, TypeRef] = {}
                if f is not None:
                    nb = self._bindings_for(substitute(f.type, bindings), r.nested_qname)
                sub_rows = self.rows_for_type(r.nested_qname, nb, location=r.location)
                tables.append(TypeTable(qname=r.nested_qname, name=t.name, title=t.name,
                                        description=(t.javadoc.text if t.javadoc else t.comment) or "",
                                        fields=sub_rows, side=side))
                self._collect_nested(sub_rows, r.nested_qname, nb, side, visited, tables, enums)
