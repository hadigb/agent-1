"""Helpers shared by all detectors."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..javaparse.model import Annotation, Javadoc, MethodDecl, Param, TypeDecl, TypeRef, find_annotation

SIMPLE_TYPES = {
    "String", "int", "Integer", "long", "Long", "short", "Short", "byte", "Byte", "boolean", "Boolean",
    "double", "Double", "float", "Float", "char", "Character", "BigDecimal", "BigInteger", "UUID",
    "LocalDate", "LocalDateTime", "LocalTime", "Instant", "Date", "ZonedDateTime", "OffsetDateTime",
    "Number", "Object", "CharSequence", "Duration", "Period",
}

REQUIRED_ANNOTATIONS = {"NotNull", "NotBlank", "NotEmpty", "NonNull", "Nonnull", "Required"}
VALIDATION_ANNOTATIONS = REQUIRED_ANNOTATIONS | {"Size", "Min", "Max", "Pattern", "Positive", "PositiveOrZero",
                                                 "Negative", "NegativeOrZero", "Email", "Digits", "Past", "Future",
                                                 "DecimalMin", "DecimalMax", "Length", "Range", "Valid"}
PRIMITIVE_TYPE_NAMES = {"int", "long", "short", "byte", "boolean", "double", "float", "char"}

DEFAULT_RESPONSE_WRAPPERS = [
    "ResponseEntity", "HttpEntity", "RequestEntity", "Mono", "CompletableFuture", "CompletionStage", "Future",
    "Callable", "DeferredResult", "ListenableFuture", "Optional", "Uni", "Single", "Maybe", "HttpResponse",
    "Publisher", "Response",
]
COLLECTION_WRAPPERS = ["Flux", "Multi", "Observable", "Flowable"]


def normalize_path(*parts: Optional[str]) -> str:
    segs: List[str] = []
    for p in parts:
        if not p:
            continue
        p = p.strip()
        if not p:
            continue
        segs.append(p.strip("/"))
    path = "/" + "/".join(s for s in segs if s)
    path = re.sub(r"/{2,}", "/", path)
    # Spring/JAX-RS regex path variables {id:[0-9]+} -> {id}
    path = re.sub(r"\{([^}:]+):[^}]*\}", r"{\1}", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return path


def first_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, list):
        return first_str(v[0]) if v else None
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def all_strs(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        out: List[str] = []
        for x in v:
            out.extend(all_strs(x))
        return out
    return [str(v)]


def constant_name(v: Any) -> Optional[str]:
    """RequestMethod.POST -> POST ; MediaType.APPLICATION_JSON_VALUE -> APPLICATION_JSON_VALUE"""
    s = first_str(v)
    if s is None:
        return None
    return s.rsplit(".", 1)[-1]


_MEDIA_CONSTANTS = {
    "APPLICATION_JSON_VALUE": "application/json", "APPLICATION_JSON": "application/json",
    "APPLICATION_XML_VALUE": "application/xml", "APPLICATION_XML": "application/xml",
    "TEXT_PLAIN_VALUE": "text/plain", "TEXT_PLAIN": "text/plain",
    "MULTIPART_FORM_DATA_VALUE": "multipart/form-data", "MULTIPART_FORM_DATA": "multipart/form-data",
    "APPLICATION_FORM_URLENCODED_VALUE": "application/x-www-form-urlencoded",
    "APPLICATION_FORM_URLENCODED": "application/x-www-form-urlencoded",
    "APPLICATION_OCTET_STREAM_VALUE": "application/octet-stream", "TEXT_HTML_VALUE": "text/html",
    "APPLICATION_PDF_VALUE": "application/pdf", "WILDCARD": "*/*", "APPLICATION_JSON_UTF8_VALUE": "application/json",
}


def media_types(v: Any) -> List[str]:
    out: List[str] = []
    for s in all_strs(v):
        key = s.rsplit(".", 1)[-1]
        out.append(_MEDIA_CONSTANTS.get(key, s))
    return out


def is_simple_type(ref: TypeRef) -> bool:
    return ref.simple_name in SIMPLE_TYPES and not ref.args


def unwrap_response(ref: Optional[TypeRef], wrappers: Sequence[str] = DEFAULT_RESPONSE_WRAPPERS) -> Optional[TypeRef]:
    """Strip ResponseEntity<T>, Mono<T> ... down to T; Flux<T> becomes List<T>."""
    if ref is None:
        return None
    cur = ref
    for _ in range(6):
        n = cur.simple_name
        if n in wrappers and cur.args and not cur.args[0].wildcard:
            cur = cur.args[0]
            continue
        if n in wrappers and cur.args and cur.args[0].wildcard and cur.args[0].args:
            cur = cur.args[0].args[0]
            continue
        if n in COLLECTION_WRAPPERS and cur.args:
            return TypeRef(name="List", args=[cur.args[0]])
        break
    if cur.simple_name == "void" or cur.simple_name == "Void":
        return None
    return cur


# ---------------------------------------------------------------------- #
# annotation-driven field metadata
#
# Each piece of metadata (required?, description, wire name, ...) is read
# from whichever annotation the field happens to carry. Rather than a long
# if/elif chain keyed on the annotation's simple name, every recognised
# annotation is registered once in a small table together with the function
# that knows how to read *that* annotation. Looking a field up then becomes
# "iterate the annotations, dispatch on name, stop at the first decisive
# answer" - the same behaviour as the original chain, only declarative.
# ---------------------------------------------------------------------- #
RequiredReader = Callable[[Annotation], Optional[bool]]


def _required_always_true(_a: Annotation) -> Optional[bool]:
    return True


def _required_always_false(_a: Annotation) -> Optional[bool]:
    return False


def _required_from_schema(a: Annotation) -> Optional[bool]:
    if a.get("required") is True:
        return True
    mode = constant_name(a.get("requiredMode"))
    if mode == "REQUIRED":
        return True
    if mode == "NOT_REQUIRED":
        return False
    return None


def _required_from_flag(a: Annotation) -> Optional[bool]:
    if a.get("required") is not None:
        return bool(a.get("required"))
    return None


_REQUIRED_READERS: Dict[str, RequiredReader] = {
    **{name: _required_always_true for name in REQUIRED_ANNOTATIONS},
    "Schema": _required_from_schema,
    "ApiModelProperty": _required_from_flag,
    "JsonProperty": _required_from_flag,
    "Parameter": _required_from_flag,
    "ApiParam": _required_from_flag,
    "Nullable": _required_always_false,
}


def required_from_annotations(annotations: List[Annotation], type_ref: Optional[TypeRef] = None) -> Optional[bool]:
    """True/False when an annotation states it explicitly, None when unknown."""
    for a in annotations:
        reader = _REQUIRED_READERS.get(a.simple_name)
        if reader is None:
            continue
        result = reader(a)
        if result is not None:
            return result
    if type_ref is not None:
        if type_ref.simple_name == "Optional":
            return False
        if type_ref.name in PRIMITIVE_TYPE_NAMES and type_ref.dims == 0:
            return True
    return None


DescriptionReader = Callable[[Annotation], str]


def _description_via(*keys: str) -> DescriptionReader:
    def read(a: Annotation) -> str:
        return a.get_str(*keys) or ""
    return read


_DESCRIPTION_READERS: Dict[str, DescriptionReader] = {
    "Schema": _description_via("description"),
    "Parameter": _description_via("description"),
    "ApiModelProperty": _description_via("value", "notes"),
    "ApiParam": _description_via("value", "notes"),
    "JsonPropertyDescription": _description_via("value"),
    "Comment": _description_via("value"),
    "Description": _description_via("value"),
}


def description_from_annotations(annotations: List[Annotation]) -> str:
    for a in annotations:
        reader = _DESCRIPTION_READERS.get(a.simple_name)
        if reader is None:
            continue
        text = reader(a)
        if text:
            return text
    return ""


_WIRE_NAME_ANNOTATIONS = (
    "Schema", "ApiModelProperty", "Parameter", "ApiParam",
    "JsonProperty", "SerializedName", "JsonAlias", "XmlElement", "XmlAttribute", "JsonbProperty",
)


def wire_name(annotations: List[Annotation], java_name: str) -> str:
    """Jackson / Gson / JAXB renames."""
    for a in annotations:
        if a.simple_name in _WIRE_NAME_ANNOTATIONS:
            v = a.get_str("value", "name")
            if v:
                return v
    return java_name


_IGNORED_FIELD_ANNOTATIONS = ("JsonIgnore", "Transient", "JsonBackReference", "XmlTransient")


def is_ignored_field(annotations: List[Annotation]) -> bool:
    return any(a.simple_name in _IGNORED_FIELD_ANNOTATIONS for a in annotations)


def method_summary(m: MethodDecl) -> str:
    a = find_annotation(m.annotations, "Operation")
    if a:
        s = a.get_str("summary") or a.get_str("description")
        if s:
            return s
    a = find_annotation(m.annotations, "ApiOperation")
    if a:
        s = a.get_str("value") or a.get_str("notes")
        if s:
            return s
    if m.javadoc and m.javadoc.text:
        return m.javadoc.text
    return m.comment or ""


def method_description(m: MethodDecl) -> str:
    parts: List[str] = []
    a = find_annotation(m.annotations, "Operation")
    if a and a.get_str("description"):
        parts.append(a.get_str("description") or "")
    a = find_annotation(m.annotations, "ApiOperation")
    if a and a.get_str("notes"):
        parts.append(a.get_str("notes") or "")
    if m.javadoc and m.javadoc.text:
        parts.append(m.javadoc.text)
    elif m.comment:
        parts.append(m.comment)
    return " ".join(p for p in parts if p)


def param_description(p: Param, javadoc: Optional[Javadoc]) -> str:
    d = description_from_annotations(p.annotations)
    if d:
        return d
    if javadoc and p.name in javadoc.params:
        return javadoc.params[p.name]
    return ""


def is_deprecated(annotations: List[Annotation], javadoc: Optional[Javadoc]) -> bool:
    if any(a.simple_name == "Deprecated" for a in annotations):
        return True
    return bool(javadoc and "deprecated" in javadoc.tags)


def resolve_constant_in_type(t: TypeDecl, name: str) -> Optional[str]:
    """Value of a `static final String NAME = "..."` constant declared in the type."""
    from .constants import ConstantResolver

    value = ConstantResolver(t).expand(name)
    return value if isinstance(value, str) and value != name else None


def expand_constants(raw: Any, t: TypeDecl, index: Any = None) -> Any:
    """Turn `BASE + "/x"` / `Routes.FOO` annotation values into concrete strings."""
    from .constants import expand_constants as _expand

    return _expand(raw, t, index)
