"""Builds symbol rows and edges (the code graph) from the parsed files."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ..javaparse.model import JavaFile, MethodDecl, TypeDecl, TypeRef
from .index import BUILTIN_TYPES, CodeIndex, ResolveContext
from .store import SymbolRow

Edge = Tuple[str, str, str, Optional[Dict[str, Any]]]


def ext(name: str) -> str:
    return f"ext:{name}"


class GraphBuilder:
    def __init__(self, index: CodeIndex) -> None:
        self.index = index
        self.symbols: List[SymbolRow] = []
        self.edges: List[Edge] = []
        self._edge_set: Set[Tuple[str, str, str]] = set()

    # ------------------------------------------------------------------ public
    def build(self) -> Tuple[List[SymbolRow], List[Edge]]:
        for qname, t in self.index.types.items():
            self._build_type(t)
        return self.symbols, self.edges

    # ------------------------------------------------------------------ helpers
    def _edge(self, src: str, dst: str, kind: str, meta: Optional[Dict[str, Any]] = None) -> None:
        key = (src, dst, kind)
        if key in self._edge_set:
            return
        self._edge_set.add(key)
        self.edges.append((src, dst, kind, meta))

    def _type_edges(self, src: str, ref: TypeRef, kind: str, ctx: ResolveContext, tps: Set[str]) -> None:
        stack = [ref]
        while stack:
            r = stack.pop()
            if not r.wildcard:
                res = self.index.resolve(r.name, ctx, tps)
                if res.ok:
                    self._edge(src, res.qname, kind, {"guessed": True} if res.kind == "guessed" else None)
                elif res.kind == "external":
                    self._edge(src, ext(r.simple_name), kind)
            stack.extend(r.args)

    def _build_type(self, t: TypeDecl) -> None:
        jf = self.index.type_file[t.qname]
        ctx = self.index.context_for(t.qname)
        data = t.to_dict()
        data["nested"] = [n.qname for n in t.nested]
        # members are stored as their own symbol rows
        data["fields"] = [f.name for f in t.fields]
        data["methods"] = [m.signature_key() for m in t.methods]
        data["constructors"] = [m.signature_key() for m in t.constructors]
        self.symbols.append(SymbolRow(qname=t.qname, kind="type", name=t.name, type_kind=t.kind,
                                      file_path=jf.path, parent=t.outer_qname, start_line=t.start_line,
                                      end_line=t.end_line, data=data))
        if t.outer_qname:
            self._edge(t.outer_qname, t.qname, "contains")
        for ref in t.extends:
            self._type_edges(t.qname, ref, "extends", ctx, set())
        for ref in t.implements:
            self._type_edges(t.qname, ref, "implements", ctx, set())
        for a in t.annotations:
            self._edge(t.qname, ext(a.simple_name), "annotated_with")
        # fields
        for f in t.fields:
            fq = CodeIndex.field_qname(t.qname, f.name)
            self.symbols.append(SymbolRow(qname=fq, kind="constant" if (f.is_static and "final" in f.modifiers) else "field",
                                          name=f.name, type_kind=None, file_path=jf.path, parent=t.qname,
                                          start_line=f.start_line, end_line=f.end_line, data=f.to_dict()))
            self._edge(t.qname, fq, "contains")
            self._type_edges(fq, f.type, "field_type", ctx, set())
            for a in f.annotations:
                self._edge(fq, ext(a.simple_name), "annotated_with")
        for c in t.enum_constants:
            cq = CodeIndex.field_qname(t.qname, c.name)
            self.symbols.append(SymbolRow(qname=cq, kind="constant", name=c.name, type_kind=None, file_path=jf.path,
                                          parent=t.qname, start_line=c.line, end_line=c.line, data=c.to_dict()))
            self._edge(t.qname, cq, "contains")
        for p in t.record_components:
            fq = CodeIndex.field_qname(t.qname, p.name)
            self.symbols.append(SymbolRow(qname=fq, kind="field", name=p.name, type_kind=None, file_path=jf.path,
                                          parent=t.qname, start_line=t.start_line, end_line=t.start_line,
                                          data={"name": p.name, "type": p.type.to_dict(), "modifiers": ["private", "final"],
                                                "annotations": [a.to_dict() for a in p.annotations], "record_component": True}))
            self._edge(t.qname, fq, "contains")
            self._type_edges(fq, p.type, "field_type", ctx, set(t.type_params))
        # methods
        for m in t.methods + t.constructors:
            self._build_method(t, m, ctx, jf)

    def _build_method(self, t: TypeDecl, m: MethodDecl, ctx: ResolveContext, jf: JavaFile) -> None:
        mq = CodeIndex.method_qname(t.qname, m)
        tps = set(m.type_params)
        data = m.to_dict()
        self.symbols.append(SymbolRow(qname=mq, kind="method", name=m.name, type_kind=None, file_path=jf.path,
                                      parent=t.qname, start_line=m.start_line, end_line=m.end_line, data=data))
        self._edge(t.qname, mq, "contains")
        if m.return_type is not None:
            self._type_edges(mq, m.return_type, "returns", ctx, tps)
        for p in m.params:
            self._type_edges(mq, p.type, "param_type", ctx, tps)
        for th in m.throws:
            self._type_edges(mq, th, "throws", ctx, tps)
        for a in m.annotations:
            self._edge(mq, ext(a.simple_name), "annotated_with")
        if m.body is None:
            return
        body = m.body
        param_types: Dict[str, TypeRef] = {p.name: p.type for p in m.params}
        # creations & thrown types
        for cname in body.creations:
            res = self.index.resolve(cname, ctx, tps)
            if res.ok:
                self._edge(mq, res.qname, "creates")
        for th in body.throws:
            if th.type_name:
                res = self.index.resolve(th.type_name, ctx, tps)
                if res.ok:
                    self._edge(mq, res.qname, "throws", {"site": th.line})
                elif res.kind == "external":
                    self._edge(mq, ext(th.type_name.rsplit(".", 1)[-1]), "throws", {"site": th.line})
            for ref in th.refs:
                self._const_ref(mq, ref, ctx, tps, {"in_throw": True})
        for ref in body.refs:
            self._const_ref(mq, ref, ctx, tps, None)
        # type mentions inside the body
        for name in body.type_mentions:
            if name in BUILTIN_TYPES:
                continue
            res = self.index.resolve(name, ctx, tps)
            if res.ok:
                self._edge(mq, res.qname, "mentions")
        # calls
        for call in body.calls:
            target = self._resolve_call(t, m, call.receiver, call.name, call.argc, ctx, tps, param_types, body.locals)
            if target:
                for tq, meta in target:
                    self._edge(mq, tq, "calls", meta)
            else:
                self._edge(mq, ext(f"{'.'.join(call.receiver) + '.' if call.receiver else ''}{call.name}()"), "calls",
                           {"unresolved": True})

    def _const_ref(self, mq: str, ref: str, ctx: ResolveContext, tps: Set[str], meta: Optional[Dict[str, Any]]) -> None:
        type_name, member = ref.split(".", 1)
        res = self.index.resolve(type_name, ctx, tps)
        if res.ok and self.index.has_constant(res.qname, member):
            self._edge(mq, CodeIndex.field_qname(res.qname, member), "uses_const", meta)

    def _resolve_receiver_type(self, t: TypeDecl, receiver: List[str], ctx: ResolveContext, tps: Set[str],
                               param_types: Dict[str, TypeRef], locals_: Dict[str, str]) -> Optional[Tuple[str, bool]]:
        """Return (type qname, is_static_context) for a receiver chain, or None."""
        chain = list(receiver)
        if chain and chain[0] == "this":
            chain = chain[1:]
            if not chain:
                return t.qname, False
        if not chain:
            return t.qname, False
        if chain[0] == "()":
            return None
        if chain[0] == "super":
            sups = self.index._supertypes_qnames(t.qname)
            return (sups[0], False) if sups else None
        head = chain[0]
        cur: Optional[str] = None
        static_ctx = False
        if head in param_types:
            res = self.index.resolve(param_types[head].name, ctx, tps)
            cur = res.qname if res.ok else None
        elif head in locals_:
            res = self.index.resolve(locals_[head], ctx, tps)
            cur = res.qname if res.ok else None
        else:
            ft = self.index.field_type(t.qname, head)
            if ft is not None:
                res = self.index.resolve(ft[0].name, ctx, tps)
                cur = res.qname if res.ok else None
            elif head[:1].isupper():
                res = self.index.resolve(head, ctx, tps)
                if res.ok:
                    cur = res.qname
                    static_ctx = True
                elif len(chain) > 1:
                    # maybe a qualified type name a.b.C written inline - try progressively
                    for k in range(2, len(chain) + 1):
                        res = self.index.resolve(".".join(chain[:k]), ctx, tps)
                        if res.ok:
                            cur = res.qname
                            static_ctx = True
                            chain = chain[k - 1:]
                            break
        if cur is None:
            return None
        for seg in chain[1:]:
            ft = self.index.field_type(cur, seg)
            if ft is None:
                # enum constant: type stays the same (Status.ACTIVE.getCode())
                if self.index.has_constant(cur, seg):
                    static_ctx = False
                    continue
                return None
            res = self.index.resolve(ft[0].name, self.index.context_for(ft[1]), set())
            if not res.ok:
                return None
            cur = res.qname
            static_ctx = False
        return cur, static_ctx

    def _resolve_call(self, t: TypeDecl, m: MethodDecl, receiver: List[str], name: str, argc: int,
                      ctx: ResolveContext, tps: Set[str], param_types: Dict[str, TypeRef],
                      locals_: Dict[str, str]) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
        if not receiver or receiver == ["this"]:
            # unqualified call: a method of this type (or an ancestor) - otherwise maybe a static import
            if not self.index.find_method(t.qname, name, argc, include_impls=False):
                for imp in ctx.file.imports:
                    if imp.static and (imp.name.rsplit(".", 1)[-1] == name or imp.wildcard):
                        owner = imp.name if imp.wildcard else imp.name.rsplit(".", 1)[0]
                        if owner in self.index.types:
                            found = self.index.find_method(owner, name, argc, include_impls=False)
                            if found:
                                return [(CodeIndex.method_qname(o, mm), None) for mm, o in found[:1]]
                return []
        rt = self._resolve_receiver_type(t, receiver, ctx, tps, param_types, locals_)
        if rt is None:
            return []
        type_q, _static = rt
        found = self.index.find_method(type_q, name, argc, include_impls=False)
        out: List[Tuple[str, Optional[Dict[str, Any]]]] = []
        if found:
            mm, owner = found[0]
            out.append((CodeIndex.method_qname(owner, mm), None))
            if mm.body is None:  # abstract / interface: link implementations too
                for impl_q in self.index.implementations(owner):
                    impl_found = self.index.find_method(impl_q, name, argc, include_impls=False)
                    for im, io in impl_found[:1]:
                        if io == impl_q and im.body is not None:
                            out.append((CodeIndex.method_qname(io, im), {"impl": True}))
            return out
        # no method with that name on the type itself: try implementations (e.g. interface without the method)
        for impl_q in self.index.implementations(type_q):
            impl_found = self.index.find_method(impl_q, name, argc, include_impls=False)
            for im, io in impl_found[:1]:
                out.append((CodeIndex.method_qname(io, im), {"impl": True}))
        return out


def build_graph(files: Iterable[JavaFile]) -> Tuple[CodeIndex, List[SymbolRow], List[Edge]]:
    index = CodeIndex(files)
    symbols, edges = GraphBuilder(index).build()
    return index, symbols, edges
