"""In-memory index over parsed Java files with name resolution helpers.

The index is rebuilt from the per-file parse cache on every scan and is the
single place that knows how to turn a type name written in source into the
qualified name of a project type (or decide that it is a JDK/builtin or an
external library type).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from ..javaparse.model import FieldDecl, JavaFile, MethodDecl, TypeDecl, TypeRef

# Well-known JDK types we never try to resolve to project types.
BUILTIN_TYPES: Set[str] = set("""
String Integer Long Boolean Object Number Double Float Short Byte Character Void Iterable Comparable Runnable
Exception RuntimeException Throwable Error Enum Record Class Thread StringBuilder StringBuffer Math System
Override Deprecated SuppressWarnings FunctionalInterface SafeVarargs CharSequence Cloneable AutoCloseable
List Map Set Collection ArrayList HashMap LinkedHashMap HashSet LinkedHashSet Optional Date UUID Locale Arrays
Collections Objects Iterator Queue Deque TreeMap TreeSet LinkedList Stream Collectors Function Supplier Consumer
Predicate BiFunction Callable Future CompletableFuture Executor ExecutorService TimeUnit
BigDecimal BigInteger LocalDate LocalDateTime LocalTime Instant ZonedDateTime OffsetDateTime Duration Period
Calendar Timestamp Serializable IOException Currency Pattern Matcher Files Path Paths File InputStream OutputStream
Reader Writer Optional OptionalInt OptionalLong OptionalDouble IntStream LongStream
boolean byte char short int long float double void var
""".split())

PRIMITIVE_TYPES = {"boolean", "byte", "char", "short", "int", "long", "float", "double", "void"}


@dataclass
class Resolution:
    kind: str                      # project | typevar | builtin | external | guessed
    qname: Optional[str] = None    # for project/guessed

    @property
    def ok(self) -> bool:
        return self.kind in ("project", "guessed") and self.qname is not None


@dataclass
class ResolveContext:
    file: JavaFile
    enclosing: List[TypeDecl] = field(default_factory=list)   # outermost -> innermost
    type_params: Set[str] = field(default_factory=set)


class CodeIndex:
    def __init__(self, files: Iterable[JavaFile]) -> None:
        self.files: Dict[str, JavaFile] = {}
        self.types: Dict[str, TypeDecl] = {}
        self.type_file: Dict[str, JavaFile] = {}
        self.type_outer: Dict[str, Optional[str]] = {}
        self.simple: Dict[str, List[str]] = {}
        self.package_types: Dict[str, Set[str]] = {}
        self._super_cache: Dict[str, List[str]] = {}
        self._super_in_progress: Set[str] = set()
        self._subtypes: Optional[Dict[str, List[str]]] = None
        self._lock = threading.RLock()   # the index is shared read-only between UI threads; lazy caches need a lock
        for jf in files:
            self.add_file(jf)

    def add_file(self, jf: JavaFile) -> None:
        self.files[jf.path] = jf
        for t in jf.all_types():
            self.types[t.qname] = t
            self.type_file[t.qname] = jf
            self.type_outer[t.qname] = t.outer_qname
            self.simple.setdefault(t.name, []).append(t.qname)
            self.package_types.setdefault(jf.package, set()).add(t.qname)

    # ------------------------------------------------------------------ context helpers
    def context_for(self, qname: str) -> ResolveContext:
        """Build a resolve context for code inside type ``qname``."""
        jf = self.type_file[qname]
        chain: List[TypeDecl] = []
        cur: Optional[str] = qname
        while cur is not None:
            t = self.types[cur]
            chain.insert(0, t)
            cur = t.outer_qname
        tps: Set[str] = set()
        for t in chain:
            tps.update(t.type_params)
        return ResolveContext(file=jf, enclosing=chain, type_params=tps)

    # ------------------------------------------------------------------ resolution
    def resolve(self, name: str, ctx: ResolveContext, extra_type_params: Optional[Set[str]] = None) -> Resolution:
        if not name or name == "?":
            return Resolution("builtin")
        if name in PRIMITIVE_TYPES:
            return Resolution("builtin")
        tps = ctx.type_params | (extra_type_params or set())
        if name in tps:
            return Resolution("typevar")
        if name in self.types:
            return Resolution("project", name)
        if "." in name:
            first, rest = name.split(".", 1)
            r = self._resolve_simple(first, ctx, tps)
            if r.ok:
                cand = f"{r.qname}.{rest}"
                if cand in self.types:
                    return Resolution("project", cand)
                return Resolution("external")
            # maybe a fully qualified prefix of a project type in a different form
            if name.rsplit(".", 1)[-1] in self.simple:
                for q in self.simple[name.rsplit(".", 1)[-1]]:
                    if q == name or q.endswith("." + name):
                        return Resolution("project", q)
            return Resolution("external")
        return self._resolve_simple(name, ctx, tps)

    # _resolve_simple mirrors Java's own name-lookup order: nested types of
    # enclosing types, then explicit imports, then same package, then
    # wildcard imports, then (as a last-resort heuristic) a unique simple-name
    # match anywhere in the project. Each stage is its own method so the
    # overall order reads as a short list of stage calls instead of one long
    # function; every stage returns None to mean "not found here, try the
    # next stage" - same short-circuiting the original if-chain did.
    def _resolve_simple(self, name: str, ctx: ResolveContext, tps: Set[str]) -> Resolution:
        if name in tps:
            return Resolution("typevar")
        for stage in (self._resolve_in_enclosing, self._resolve_via_import, self._resolve_same_package,
                     self._resolve_via_wildcard_import):
            res = stage(name, ctx)
            if res is not None:
                return res
        if name in BUILTIN_TYPES:
            return Resolution("builtin")
        return self._resolve_unique_guess(name, ctx)

    def _resolve_in_enclosing(self, name: str, ctx: ResolveContext) -> Optional[Resolution]:
        """Nested types of enclosing types (innermost first) and of their supertypes."""
        for enc in reversed(ctx.enclosing):
            cand = f"{enc.qname}.{name}"
            if cand in self.types:
                return Resolution("project", cand)
            for sup in self._supertypes_qnames(enc.qname):
                cand = f"{sup}.{name}"
                if cand in self.types:
                    return Resolution("project", cand)
            if enc.name == name:
                return Resolution("project", enc.qname)
        return None

    def _resolve_via_import(self, name: str, ctx: ResolveContext) -> Optional[Resolution]:
        for imp in ctx.file.imports:
            if imp.static or imp.wildcard:
                continue
            if imp.name.rsplit(".", 1)[-1] == name:
                if imp.name in self.types:
                    return Resolution("project", imp.name)
                # imported nested type written as a.b.Outer.Inner
                return Resolution("external") if name not in BUILTIN_TYPES else Resolution("builtin")
        return None

    def _resolve_same_package(self, name: str, ctx: ResolveContext) -> Optional[Resolution]:
        cand = f"{ctx.file.package}.{name}" if ctx.file.package else name
        if cand in self.types:
            return Resolution("project", cand)
        return None

    def _resolve_via_wildcard_import(self, name: str, ctx: ResolveContext) -> Optional[Resolution]:
        for imp in ctx.file.imports:
            if imp.wildcard and not imp.static:
                cand = f"{imp.name}.{name}"
                if cand in self.types:
                    return Resolution("project", cand)
                # wildcard import of a class's nested types: import a.b.Outer.*;
        return None

    def _resolve_unique_guess(self, name: str, ctx: ResolveContext) -> Resolution:
        """Last resort: a unique simple-name match anywhere in the project."""
        cands = self.simple.get(name, [])
        if not cands:
            return Resolution("external")
        if len(cands) == 1:
            return Resolution("guessed", cands[0])
        pkg = ctx.file.package
        same_pkg = [c for c in cands if c.rsplit(".", 1)[0] == pkg]
        if len(same_pkg) == 1:
            return Resolution("guessed", same_pkg[0])
        root = pkg.split(".")[0] if pkg else ""
        same_root = [c for c in cands if c.startswith(root + ".")]
        if len(same_root) == 1:
            return Resolution("guessed", same_root[0])
        return Resolution("external")

    def resolve_typeref(self, ref: TypeRef, ctx: ResolveContext, extra_type_params: Optional[Set[str]] = None) -> Resolution:
        return self.resolve(ref.name, ctx, extra_type_params)

    def typeref_project_types(self, ref: TypeRef, ctx: ResolveContext,
                              extra_type_params: Optional[Set[str]] = None) -> List[str]:
        """All project type qnames mentioned anywhere in a (generic) type reference."""
        out: List[str] = []
        stack = [ref]
        while stack:
            r = stack.pop()
            if not r.wildcard:
                res = self.resolve(r.name, ctx, extra_type_params)
                if res.ok and res.qname not in out:
                    out.append(res.qname)
            stack.extend(r.args)
        return out

    # ------------------------------------------------------------------ hierarchy
    def _supertypes_qnames(self, qname: str) -> List[str]:
        """Direct resolved supertypes (extends + implements) of a project type."""
        cached = self._super_cache.get(qname)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._super_cache.get(qname)
            if cached is not None:
                return cached
            if qname in self._super_in_progress:
                return []
            t = self.types.get(qname)
            if t is None:
                return []
            self._super_in_progress.add(qname)
            try:
                ctx = self.context_for(qname)
                out: List[str] = []
                for ref in t.extends + t.implements:
                    res = self.resolve(ref.name, ctx)
                    if res.ok and res.qname != qname:
                        out.append(res.qname)
            finally:
                self._super_in_progress.discard(qname)
            self._super_cache[qname] = out
            return out

    def superclass_chain(self, qname: str) -> List[str]:
        """qname followed by all resolved ancestors (breadth first, no duplicates)."""
        seen: List[str] = []
        queue = [qname]
        while queue:
            cur = queue.pop(0)
            if cur in seen:
                continue
            seen.append(cur)
            queue.extend(self._supertypes_qnames(cur))
        return seen

    def subtypes(self, qname: str) -> List[str]:
        if self._subtypes is None:
            with self._lock:
                if self._subtypes is None:
                    sub: Dict[str, List[str]] = {}
                    for q in self.types:
                        for sup in self._supertypes_qnames(q):
                            sub.setdefault(sup, []).append(q)
                    self._subtypes = sub
        return self._subtypes.get(qname, [])

    def implementations(self, qname: str) -> List[str]:
        """All transitive concrete subtypes of a type."""
        out: List[str] = []
        queue = list(self.subtypes(qname))
        while queue:
            cur = queue.pop(0)
            if cur in out:
                continue
            out.append(cur)
            queue.extend(self.subtypes(cur))
        return out

    # ------------------------------------------------------------------ members
    def all_fields(self, qname: str, include_static: bool = False) -> List[Tuple[FieldDecl, str]]:
        """Instance fields of a type including those inherited from project supertypes (own first)."""
        per_owner: List[Tuple[str, List[FieldDecl]]] = []
        seen: Set[str] = set()
        # most derived class first so that shadowed fields keep the derived definition ...
        for owner in self.superclass_chain(qname):
            t = self.types[owner]
            if t.kind == "interface" and owner != qname:
                continue
            mine: List[FieldDecl] = []
            for f in t.fields:
                if f.is_static and not include_static:
                    continue
                if f.name in seen:
                    continue
                seen.add(f.name)
                mine.append(f)
            if t.kind == "record":
                for p in t.record_components:
                    if p.name not in seen:
                        seen.add(p.name)
                        mine.append(FieldDecl(name=p.name, type=p.type, modifiers=["private", "final"],
                                              annotations=p.annotations, javadoc=None, comment="",
                                              start_line=t.start_line, end_line=t.start_line, source=""))
            per_owner.append((owner, mine))
        # ... but base-class fields (the response envelope, typically) are listed first, as serialisers do.
        out: List[Tuple[FieldDecl, str]] = []
        for owner, mine in reversed(per_owner):
            out.extend((f, owner) for f in mine)
        return out

    def find_method(self, qname: str, name: str, argc: Optional[int] = None,
                    include_impls: bool = True) -> List[Tuple[MethodDecl, str]]:
        """Find methods named ``name`` in a type, its ancestors and (optionally) its implementations."""
        out: List[Tuple[MethodDecl, str]] = []
        owners = self.superclass_chain(qname)
        if include_impls:
            owners += [q for q in self.implementations(qname) if q not in owners]
        for owner in owners:
            t = self.types.get(owner)
            if t is None:
                continue
            for m in t.methods:
                if m.name != name:
                    continue
                if argc is not None:
                    n = len(m.params)
                    varargs = bool(m.params) and m.params[-1].varargs
                    if not (n == argc or (varargs and argc >= n - 1)):
                        continue
                out.append((m, owner))
        return out

    def field_type(self, qname: str, field_name: str) -> Optional[Tuple[TypeRef, str]]:
        for f, owner in self.all_fields(qname, include_static=True):
            if f.name == field_name:
                return f.type, owner
        return None

    def has_constant(self, qname: str, name: str) -> bool:
        t = self.types.get(qname)
        if t is None:
            return False
        if any(c.name == name for c in t.enum_constants):
            return True
        return any(f.name == name and f.is_static for f in t.fields)

    @staticmethod
    def method_qname(owner: str, m: MethodDecl) -> str:
        return f"{owner}#{m.signature_key()}"

    @staticmethod
    def field_qname(owner: str, name: str) -> str:
        return f"{owner}#{name}"
