"""Data model produced by the Java declaration parser.

Everything here is a plain dataclass that can be serialised to JSON (see
``to_dict`` / ``from_dict``) so parse results can be cached per file hash in
the graph database and never re-parsed unless the file changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Types
# --------------------------------------------------------------------------- #
@dataclass
class TypeRef:
    """A type as written in the source: ``List<DocumentItem>``, ``int[]`` ..."""

    name: str                      # simple or qualified name as written ("List", "java.util.List", "Map.Entry")
    args: List["TypeRef"] = field(default_factory=list)
    dims: int = 0                  # array dimensions
    wildcard: Optional[str] = None  # None | "?" | "? extends" | "? super" (bound in args[0])
    annotations: List[str] = field(default_factory=list)

    @property
    def simple_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    def canonical(self) -> str:
        if self.wildcard:
            if self.args:
                return f"{self.wildcard} {self.args[0].canonical()}"
            return "?"
        s = self.name
        if self.args:
            s += "<" + ", ".join(a.canonical() for a in self.args) + ">"
        s += "[]" * self.dims
        return s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "args": [a.to_dict() for a in self.args],
            "dims": self.dims,
            "wildcard": self.wildcard,
            "annotations": list(self.annotations),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "TypeRef":
        return TypeRef(
            name=d["name"],
            args=[TypeRef.from_dict(a) for a in d.get("args", [])],
            dims=d.get("dims", 0),
            wildcard=d.get("wildcard"),
            annotations=list(d.get("annotations", [])),
        )

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.canonical()


# --------------------------------------------------------------------------- #
# Annotations
# --------------------------------------------------------------------------- #
@dataclass
class Annotation:
    """``@Name`` or ``@Name(args)``.

    ``args`` maps element names to *decoded* values where decoding was
    possible: str for string literals / identifiers, bool, int/float, list for
    array initialisers, dict for nested annotations.  The unnamed single value
    form ``@Foo("x")`` is stored under key ``"value"``.  ``raw`` keeps the
    exact source text of the argument list for anything we could not decode.
    """

    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    line: int = 0

    @property
    def simple_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    def get(self, *keys: str, default: Any = None) -> Any:
        for k in keys:
            if k in self.args:
                return self.args[k]
        return default

    def get_str(self, *keys: str, default: Optional[str] = None) -> Optional[str]:
        """Return the first string-ish value found for ``keys`` (lists collapse to first item)."""
        v = self.get(*keys)
        if v is None:
            return default
        if isinstance(v, list):
            if not v:
                return default
            v = v[0]
        if isinstance(v, bool):
            return str(v).lower()
        return str(v)

    def get_list(self, *keys: str) -> List[Any]:
        v = self.get(*keys)
        if v is None:
            return []
        if isinstance(v, list):
            return v
        return [v]

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "args": self.args, "raw": self.raw, "line": self.line}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Annotation":
        return Annotation(name=d["name"], args=d.get("args", {}), raw=d.get("raw", ""), line=d.get("line", 0))


def find_annotation(annotations: List[Annotation], *names: str) -> Optional[Annotation]:
    for a in annotations:
        if a.simple_name in names or a.name in names:
            return a
    return None


def has_annotation(annotations: List[Annotation], *names: str) -> bool:
    return find_annotation(annotations, *names) is not None


# --------------------------------------------------------------------------- #
# Javadoc
# --------------------------------------------------------------------------- #
@dataclass
class Javadoc:
    text: str = ""                                   # main description, cleaned
    params: Dict[str, str] = field(default_factory=dict)
    returns: str = ""
    throws: Dict[str, str] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)  # other tags (deprecated, since ...)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> Optional["Javadoc"]:
        if not d:
            return None
        return Javadoc(**d)


# --------------------------------------------------------------------------- #
# Method bodies (what we keep from them for the call graph)
# --------------------------------------------------------------------------- #
@dataclass
class CallSite:
    name: str                       # method name
    receiver: List[str]             # identifier chain before the call, e.g. ["service"], ["ErrorCode"], ["this"], []
    argc: int
    line: int
    string_args: List[str] = field(default_factory=list)  # string literal arguments (for getParameter("x") etc.)
    class_args: List[str] = field(default_factory=list)   # X.class arguments (for bodyToMono(X.class) etc.)
    method_refs: List[str] = field(default_factory=list)  # "handler::method" arguments

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CallSite":
        return CallSite(**d)


@dataclass
class ThrowSite:
    type_name: Optional[str]        # for `throw new X(...)`; None for `throw var;`
    args_raw: str
    line: int
    refs: List[str] = field(default_factory=list)  # qualified identifier chains inside the args, e.g. "ErrorCode.INVALID"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ThrowSite":
        return ThrowSite(**d)


@dataclass
class BodyInfo:
    calls: List[CallSite] = field(default_factory=list)
    creations: List[str] = field(default_factory=list)      # `new X(` type names
    throws: List[ThrowSite] = field(default_factory=list)
    refs: List[str] = field(default_factory=list)           # "Type.MEMBER" chains starting with a capitalised identifier
    type_mentions: List[str] = field(default_factory=list)  # capitalised identifiers seen (candidate type usages)
    string_literals: List[str] = field(default_factory=list)
    locals: Dict[str, str] = field(default_factory=dict)    # local variable name -> declared type (as written)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "calls": [c.to_dict() for c in self.calls],
            "creations": self.creations,
            "throws": [t.to_dict() for t in self.throws],
            "refs": self.refs,
            "type_mentions": self.type_mentions,
            "string_literals": self.string_literals,
            "locals": self.locals,
        }

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> Optional["BodyInfo"]:
        if d is None:
            return None
        return BodyInfo(
            calls=[CallSite.from_dict(c) for c in d.get("calls", [])],
            creations=d.get("creations", []),
            throws=[ThrowSite.from_dict(t) for t in d.get("throws", [])],
            refs=d.get("refs", []),
            type_mentions=d.get("type_mentions", []),
            string_literals=d.get("string_literals", []),
            locals=d.get("locals", {}),
        )


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #
@dataclass
class Param:
    name: str
    type: TypeRef
    annotations: List[Annotation] = field(default_factory=list)
    varargs: bool = False
    modifiers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.to_dict(),
            "annotations": [a.to_dict() for a in self.annotations],
            "varargs": self.varargs,
            "modifiers": self.modifiers,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Param":
        return Param(
            name=d["name"],
            type=TypeRef.from_dict(d["type"]),
            annotations=[Annotation.from_dict(a) for a in d.get("annotations", [])],
            varargs=d.get("varargs", False),
            modifiers=d.get("modifiers", []),
        )


@dataclass
class FieldDecl:
    name: str
    type: TypeRef
    modifiers: List[str] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    javadoc: Optional[Javadoc] = None
    comment: str = ""                # plain // or /* */ comments directly attached
    initializer: str = ""            # raw initializer source
    start_line: int = 0
    end_line: int = 0
    source: str = ""

    @property
    def is_static(self) -> bool:
        return "static" in self.modifiers

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.to_dict(),
            "modifiers": self.modifiers,
            "annotations": [a.to_dict() for a in self.annotations],
            "javadoc": self.javadoc.to_dict() if self.javadoc else None,
            "comment": self.comment,
            "initializer": self.initializer,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "source": self.source,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "FieldDecl":
        return FieldDecl(
            name=d["name"],
            type=TypeRef.from_dict(d["type"]),
            modifiers=d.get("modifiers", []),
            annotations=[Annotation.from_dict(a) for a in d.get("annotations", [])],
            javadoc=Javadoc.from_dict(d.get("javadoc")),
            comment=d.get("comment", ""),
            initializer=d.get("initializer", ""),
            start_line=d.get("start_line", 0),
            end_line=d.get("end_line", 0),
            source=d.get("source", ""),
        )


@dataclass
class MethodDecl:
    name: str
    return_type: Optional[TypeRef]          # None for constructors
    params: List[Param] = field(default_factory=list)
    modifiers: List[str] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    javadoc: Optional[Javadoc] = None
    comment: str = ""
    throws: List[TypeRef] = field(default_factory=list)
    type_params: List[str] = field(default_factory=list)
    body: Optional[BodyInfo] = None         # None when abstract / interface / native
    is_constructor: bool = False
    start_line: int = 0
    end_line: int = 0
    source: str = ""                        # full source (signature + body)
    signature_source: str = ""              # source up to (excluding) the body

    @property
    def is_static(self) -> bool:
        return "static" in self.modifiers

    @property
    def is_public(self) -> bool:
        return "public" in self.modifiers

    def signature_key(self) -> str:
        """Stable key used in symbol qualified names: name(paramTypes)."""
        return f"{self.name}({','.join(p.type.canonical() for p in self.params)})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "return_type": self.return_type.to_dict() if self.return_type else None,
            "params": [p.to_dict() for p in self.params],
            "modifiers": self.modifiers,
            "annotations": [a.to_dict() for a in self.annotations],
            "javadoc": self.javadoc.to_dict() if self.javadoc else None,
            "comment": self.comment,
            "throws": [t.to_dict() for t in self.throws],
            "type_params": self.type_params,
            "body": self.body.to_dict() if self.body else None,
            "is_constructor": self.is_constructor,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "source": self.source,
            "signature_source": self.signature_source,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "MethodDecl":
        return MethodDecl(
            name=d["name"],
            return_type=TypeRef.from_dict(d["return_type"]) if d.get("return_type") else None,
            params=[Param.from_dict(p) for p in d.get("params", [])],
            modifiers=d.get("modifiers", []),
            annotations=[Annotation.from_dict(a) for a in d.get("annotations", [])],
            javadoc=Javadoc.from_dict(d.get("javadoc")),
            comment=d.get("comment", ""),
            throws=[TypeRef.from_dict(t) for t in d.get("throws", [])],
            type_params=d.get("type_params", []),
            body=BodyInfo.from_dict(d.get("body")),
            is_constructor=d.get("is_constructor", False),
            start_line=d.get("start_line", 0),
            end_line=d.get("end_line", 0),
            source=d.get("source", ""),
            signature_source=d.get("signature_source", ""),
        )


@dataclass
class EnumConstant:
    name: str
    args: List[str] = field(default_factory=list)   # raw argument sources
    annotations: List[Annotation] = field(default_factory=list)
    javadoc: Optional[Javadoc] = None
    comment: str = ""
    line: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "annotations": [a.to_dict() for a in self.annotations],
            "javadoc": self.javadoc.to_dict() if self.javadoc else None,
            "comment": self.comment,
            "line": self.line,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EnumConstant":
        return EnumConstant(
            name=d["name"],
            args=d.get("args", []),
            annotations=[Annotation.from_dict(a) for a in d.get("annotations", [])],
            javadoc=Javadoc.from_dict(d.get("javadoc")),
            comment=d.get("comment", ""),
            line=d.get("line", 0),
        )


@dataclass
class TypeDecl:
    kind: str                              # class | interface | enum | record | annotation
    name: str
    qname: str                             # package.Outer.Inner
    modifiers: List[str] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    javadoc: Optional[Javadoc] = None
    comment: str = ""
    type_params: List[str] = field(default_factory=list)
    extends: List[TypeRef] = field(default_factory=list)
    implements: List[TypeRef] = field(default_factory=list)
    fields: List[FieldDecl] = field(default_factory=list)
    methods: List[MethodDecl] = field(default_factory=list)
    constructors: List[MethodDecl] = field(default_factory=list)
    enum_constants: List[EnumConstant] = field(default_factory=list)
    record_components: List[Param] = field(default_factory=list)
    nested: List["TypeDecl"] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0
    source: str = ""
    header_source: str = ""                # from first modifier/annotation to the opening brace
    outer_qname: Optional[str] = None

    @property
    def is_enum(self) -> bool:
        return self.kind == "enum"

    @property
    def is_interface(self) -> bool:
        return self.kind == "interface"

    @property
    def all_methods(self) -> List[MethodDecl]:
        return self.methods + self.constructors

    def walk(self):
        """Yield this type and all nested types depth-first."""
        yield self
        for n in self.nested:
            yield from n.walk()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "qname": self.qname,
            "modifiers": self.modifiers,
            "annotations": [a.to_dict() for a in self.annotations],
            "javadoc": self.javadoc.to_dict() if self.javadoc else None,
            "comment": self.comment,
            "type_params": self.type_params,
            "extends": [t.to_dict() for t in self.extends],
            "implements": [t.to_dict() for t in self.implements],
            "fields": [f.to_dict() for f in self.fields],
            "methods": [m.to_dict() for m in self.methods],
            "constructors": [m.to_dict() for m in self.constructors],
            "enum_constants": [c.to_dict() for c in self.enum_constants],
            "record_components": [p.to_dict() for p in self.record_components],
            "nested": [n.to_dict() for n in self.nested],
            "start_line": self.start_line,
            "end_line": self.end_line,
            "source": "",  # full class source is not persisted (members carry their own source)
            "header_source": self.header_source,
            "outer_qname": self.outer_qname,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "TypeDecl":
        return TypeDecl(
            kind=d["kind"],
            name=d["name"],
            qname=d["qname"],
            modifiers=d.get("modifiers", []),
            annotations=[Annotation.from_dict(a) for a in d.get("annotations", [])],
            javadoc=Javadoc.from_dict(d.get("javadoc")),
            comment=d.get("comment", ""),
            type_params=d.get("type_params", []),
            extends=[TypeRef.from_dict(t) for t in d.get("extends", [])],
            implements=[TypeRef.from_dict(t) for t in d.get("implements", [])],
            fields=[FieldDecl.from_dict(f) for f in d.get("fields", [])],
            methods=[MethodDecl.from_dict(m) for m in d.get("methods", [])],
            constructors=[MethodDecl.from_dict(m) for m in d.get("constructors", [])],
            enum_constants=[EnumConstant.from_dict(c) for c in d.get("enum_constants", [])],
            record_components=[Param.from_dict(p) for p in d.get("record_components", [])],
            nested=[TypeDecl.from_dict(n) for n in d.get("nested", [])],
            start_line=d.get("start_line", 0),
            end_line=d.get("end_line", 0),
            source=d.get("source", ""),
            header_source=d.get("header_source", ""),
            outer_qname=d.get("outer_qname"),
        )


@dataclass
class ImportDecl:
    name: str            # fully qualified, without trailing .*
    static: bool = False
    wildcard: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ImportDecl":
        return ImportDecl(**d)


@dataclass
class JavaFile:
    path: str
    package: str = ""
    imports: List[ImportDecl] = field(default_factory=list)
    types: List[TypeDecl] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def all_types(self):
        for t in self.types:
            yield from t.walk()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "package": self.package,
            "imports": [i.to_dict() for i in self.imports],
            "types": [t.to_dict() for t in self.types],
            "errors": self.errors,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "JavaFile":
        return JavaFile(
            path=d["path"],
            package=d.get("package", ""),
            imports=[ImportDecl.from_dict(i) for i in d.get("imports", [])],
            types=[TypeDecl.from_dict(t) for t in d.get("types", [])],
            errors=d.get("errors", []),
        )
