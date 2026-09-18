"""Builds symbol rows and edges (the code graph) from the parsed files."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ..javaparse.model import EnumConstant, FieldDecl, JavaFile, MethodDecl, Param, TypeDecl, TypeRef
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

    def build(self) -> Tuple[List[SymbolRow], List[Edge]]:
        for type_decl in self.index.types.values():
            self._build_type(type_decl)
        return self.symbols, self.edges

    def _edge(self, src: str, dst: str, kind: str, meta: Optional[Dict[str, Any]] = None) -> None:
        key = (src, dst, kind)
        if key in self._edge_set:
            return
        self._edge_set.add(key)
        self.edges.append((src, dst, kind, meta))

    def _type_edges(
        self,
        src: str,
        ref: TypeRef,
        kind: str,
        ctx: ResolveContext,
        type_params: Set[str],
    ) -> None:
        stack = [ref]
        while stack:
            current = stack.pop()
            if not current.wildcard:
                resolved = self.index.resolve(current.name, ctx, type_params)
                if resolved.ok:
                    meta = {"guessed": True} if resolved.kind == "guessed" else None
                    self._edge(src, resolved.qname, kind, meta)
                elif resolved.kind == "external":
                    self._edge(src, ext(current.simple_name), kind)
            stack.extend(current.args)

    def _build_type(self, type_decl: TypeDecl) -> None:
        java_file = self.index.type_file[type_decl.qname]
        ctx = self.index.context_for(type_decl.qname)
        data = type_decl.to_dict()
        data["nested"] = [nested.qname for nested in type_decl.nested]
        data["fields"] = [field.name for field in type_decl.fields]
        data["methods"] = [method.signature_key() for method in type_decl.methods]
        data["constructors"] = [method.signature_key() for method in type_decl.constructors]
        self.symbols.append(SymbolRow(
            qname=type_decl.qname,
            kind="type",
            name=type_decl.name,
            type_kind=type_decl.kind,
            file_path=java_file.path,
            parent=type_decl.outer_qname,
            start_line=type_decl.start_line,
            end_line=type_decl.end_line,
            data=data,
        ))
        if type_decl.outer_qname:
            self._edge(type_decl.outer_qname, type_decl.qname, "contains")
        for ref in type_decl.extends:
            self._type_edges(type_decl.qname, ref, "extends", ctx, set())
        for ref in type_decl.implements:
            self._type_edges(type_decl.qname, ref, "implements", ctx, set())
        for annotation in type_decl.annotations:
            self._edge(type_decl.qname, ext(annotation.simple_name), "annotated_with")

        for field in type_decl.fields:
            self._build_field(type_decl, field, java_file, ctx)
        for constant in type_decl.enum_constants:
            self._build_enum_constant(type_decl, constant, java_file)
        for component in type_decl.record_components:
            self._build_record_component(type_decl, component, java_file, ctx)
        for method in type_decl.methods + type_decl.constructors:
            self._build_method(type_decl, method, ctx, java_file)

    def _build_field(self, type_decl: TypeDecl, field: FieldDecl, java_file: JavaFile, ctx: ResolveContext) -> None:
        field_qname = CodeIndex.field_qname(type_decl.qname, field.name)
        kind = "constant" if (field.is_static and "final" in field.modifiers) else "field"
        self.symbols.append(SymbolRow(
            qname=field_qname,
            kind=kind,
            name=field.name,
            type_kind=None,
            file_path=java_file.path,
            parent=type_decl.qname,
            start_line=field.start_line,
            end_line=field.end_line,
            data=field.to_dict(),
        ))
        self._edge(type_decl.qname, field_qname, "contains")
        self._type_edges(field_qname, field.type, "field_type", ctx, set())
        for annotation in field.annotations:
            self._edge(field_qname, ext(annotation.simple_name), "annotated_with")

    def _build_enum_constant(self, type_decl: TypeDecl, constant: EnumConstant, java_file: JavaFile) -> None:
        const_qname = CodeIndex.field_qname(type_decl.qname, constant.name)
        self.symbols.append(SymbolRow(
            qname=const_qname,
            kind="constant",
            name=constant.name,
            type_kind=None,
            file_path=java_file.path,
            parent=type_decl.qname,
            start_line=constant.line,
            end_line=constant.line,
            data=constant.to_dict(),
        ))
        self._edge(type_decl.qname, const_qname, "contains")

    def _build_record_component(
        self, type_decl: TypeDecl, component: Param, java_file: JavaFile, ctx: ResolveContext,
    ) -> None:
        field_qname = CodeIndex.field_qname(type_decl.qname, component.name)
        self.symbols.append(SymbolRow(
            qname=field_qname,
            kind="field",
            name=component.name,
            type_kind=None,
            file_path=java_file.path,
            parent=type_decl.qname,
            start_line=type_decl.start_line,
            end_line=type_decl.start_line,
            data={
                "name": component.name,
                "type": component.type.to_dict(),
                "modifiers": ["private", "final"],
                "annotations": [annotation.to_dict() for annotation in component.annotations],
                "record_component": True,
            },
        ))
        self._edge(type_decl.qname, field_qname, "contains")
        self._type_edges(field_qname, component.type, "field_type", ctx, set(type_decl.type_params))

    def _build_method(
        self, type_decl: TypeDecl, method: MethodDecl, ctx: ResolveContext, java_file: JavaFile,
    ) -> None:
        method_qname = CodeIndex.method_qname(type_decl.qname, method)
        type_params = set(method.type_params)
        self.symbols.append(SymbolRow(
            qname=method_qname,
            kind="method",
            name=method.name,
            type_kind=None,
            file_path=java_file.path,
            parent=type_decl.qname,
            start_line=method.start_line,
            end_line=method.end_line,
            data=method.to_dict(),
        ))
        self._edge(type_decl.qname, method_qname, "contains")
        if method.return_type is not None:
            self._type_edges(method_qname, method.return_type, "returns", ctx, type_params)
        for param in method.params:
            self._type_edges(method_qname, param.type, "param_type", ctx, type_params)
        for thrown in method.throws:
            self._type_edges(method_qname, thrown, "throws", ctx, type_params)
        for annotation in method.annotations:
            self._edge(method_qname, ext(annotation.simple_name), "annotated_with")
        if method.body is None:
            return
        self._build_body_edges(type_decl, method, method_qname, ctx, type_params)

    def _build_body_edges(
        self,
        type_decl: TypeDecl,
        method: MethodDecl,
        method_qname: str,
        ctx: ResolveContext,
        type_params: Set[str],
    ) -> None:
        body = method.body
        param_types: Dict[str, TypeRef] = {param.name: param.type for param in method.params}
        for created in body.creations:
            resolved = self.index.resolve(created, ctx, type_params)
            if resolved.ok:
                self._edge(method_qname, resolved.qname, "creates")
        for thrown in body.throws:
            if thrown.type_name:
                resolved = self.index.resolve(thrown.type_name, ctx, type_params)
                if resolved.ok:
                    self._edge(method_qname, resolved.qname, "throws", {"site": thrown.line})
                elif resolved.kind == "external":
                    simple = thrown.type_name.rsplit(".", 1)[-1]
                    self._edge(method_qname, ext(simple), "throws", {"site": thrown.line})
            for ref in thrown.refs:
                self._const_ref(method_qname, ref, ctx, type_params, {"in_throw": True})
        for ref in body.refs:
            self._const_ref(method_qname, ref, ctx, type_params, None)
        for name in body.type_mentions:
            if name in BUILTIN_TYPES:
                continue
            resolved = self.index.resolve(name, ctx, type_params)
            if resolved.ok:
                self._edge(method_qname, resolved.qname, "mentions")
        for call in body.calls:
            targets = self._resolve_call(
                type_decl, method, call.receiver, call.name, call.argc,
                ctx, type_params, param_types, body.locals,
            )
            if targets:
                for target_qname, meta in targets:
                    self._edge(method_qname, target_qname, "calls", meta)
            else:
                prefix = ".".join(call.receiver) + "." if call.receiver else ""
                self._edge(
                    method_qname,
                    ext(f"{prefix}{call.name}()"),
                    "calls",
                    {"unresolved": True},
                )

    def _const_ref(
        self,
        method_qname: str,
        ref: str,
        ctx: ResolveContext,
        type_params: Set[str],
        meta: Optional[Dict[str, Any]],
    ) -> None:
        type_name, member = ref.split(".", 1)
        resolved = self.index.resolve(type_name, ctx, type_params)
        if resolved.ok and self.index.has_constant(resolved.qname, member):
            self._edge(method_qname, CodeIndex.field_qname(resolved.qname, member), "uses_const", meta)

    def _resolve_receiver_type(
        self,
        type_decl: TypeDecl,
        receiver: List[str],
        ctx: ResolveContext,
        type_params: Set[str],
        param_types: Dict[str, TypeRef],
        locals_: Dict[str, str],
    ) -> Optional[Tuple[str, bool]]:
        """Return (type qname, is_static_context) for a receiver chain, or None."""
        chain = list(receiver)
        if chain and chain[0] == "this":
            chain = chain[1:]
            if not chain:
                return type_decl.qname, False
        if not chain:
            return type_decl.qname, False
        if chain[0] == "()":
            return None
        if chain[0] == "super":
            supertypes = self.index._supertypes_qnames(type_decl.qname)
            return (supertypes[0], False) if supertypes else None

        current_type, is_static = self._resolve_receiver_head(
            type_decl, chain, ctx, type_params, param_types, locals_,
        )
        if current_type is None:
            return None
        for segment in chain[1:]:
            field_info = self.index.field_type(current_type, segment)
            if field_info is None:
                # enum constant: type stays the same (Status.ACTIVE.getCode())
                if self.index.has_constant(current_type, segment):
                    is_static = False
                    continue
                return None
            field_type, owner_qname = field_info
            resolved = self.index.resolve(field_type.name, self.index.context_for(owner_qname), set())
            if not resolved.ok:
                return None
            current_type = resolved.qname
            is_static = False
        return current_type, is_static

    def _resolve_receiver_head(
        self,
        type_decl: TypeDecl,
        chain: List[str],
        ctx: ResolveContext,
        type_params: Set[str],
        param_types: Dict[str, TypeRef],
        locals_: Dict[str, str],
    ) -> Tuple[Optional[str], bool]:
        head = chain[0]
        if head in param_types:
            resolved = self.index.resolve(param_types[head].name, ctx, type_params)
            return (resolved.qname if resolved.ok else None), False
        if head in locals_:
            resolved = self.index.resolve(locals_[head], ctx, type_params)
            return (resolved.qname if resolved.ok else None), False

        field_info = self.index.field_type(type_decl.qname, head)
        if field_info is not None:
            resolved = self.index.resolve(field_info[0].name, ctx, type_params)
            return (resolved.qname if resolved.ok else None), False
        if not head[:1].isupper():
            return None, False

        resolved = self.index.resolve(head, ctx, type_params)
        if resolved.ok:
            return resolved.qname, True
        if len(chain) <= 1:
            return None, False
        # maybe a qualified type name a.b.C written inline - try progressively
        for length in range(2, len(chain) + 1):
            resolved = self.index.resolve(".".join(chain[:length]), ctx, type_params)
            if resolved.ok:
                chain[:] = chain[length - 1:]
                return resolved.qname, True
        return None, False

    def _resolve_call(
        self,
        type_decl: TypeDecl,
        method: MethodDecl,
        receiver: List[str],
        name: str,
        argc: int,
        ctx: ResolveContext,
        type_params: Set[str],
        param_types: Dict[str, TypeRef],
        locals_: Dict[str, str],
    ) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
        if not receiver or receiver == ["this"]:
            local = self.index.find_method(type_decl.qname, name, argc, include_impls=False)
            if not local:
                return self._resolve_static_import(ctx, name, argc)
        receiver_type = self._resolve_receiver_type(
            type_decl, receiver, ctx, type_params, param_types, locals_,
        )
        if receiver_type is None:
            return []
        type_qname, _is_static = receiver_type
        found = self.index.find_method(type_qname, name, argc, include_impls=False)
        if found:
            return self._calls_for_found_method(found[0], name, argc)
        return self._calls_on_implementations(type_qname, name, argc)

    def _resolve_static_import(
        self, ctx: ResolveContext, name: str, argc: int,
    ) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
        for imported in ctx.file.imports:
            if not imported.static:
                continue
            last = imported.name.rsplit(".", 1)[-1]
            if last != name and not imported.wildcard:
                continue
            owner = imported.name if imported.wildcard else imported.name.rsplit(".", 1)[0]
            if owner not in self.index.types:
                continue
            found = self.index.find_method(owner, name, argc, include_impls=False)
            if found:
                match, owner_qname = found[0]
                return [(CodeIndex.method_qname(owner_qname, match), None)]
        return []

    def _calls_for_found_method(
        self, found: Tuple[MethodDecl, str], name: str, argc: int,
    ) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
        match, owner = found
        out: List[Tuple[str, Optional[Dict[str, Any]]]] = [
            (CodeIndex.method_qname(owner, match), None),
        ]
        if match.body is not None:
            return out
        # abstract / interface: also link concrete implementations
        for impl_qname in self.index.implementations(owner):
            impl_found = self.index.find_method(impl_qname, name, argc, include_impls=False)
            for impl_method, impl_owner in impl_found[:1]:
                if impl_owner == impl_qname and impl_method.body is not None:
                    out.append((CodeIndex.method_qname(impl_owner, impl_method), {"impl": True}))
        return out

    def _calls_on_implementations(
        self, type_qname: str, name: str, argc: int,
    ) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
        out: List[Tuple[str, Optional[Dict[str, Any]]]] = []
        for impl_qname in self.index.implementations(type_qname):
            impl_found = self.index.find_method(impl_qname, name, argc, include_impls=False)
            for impl_method, impl_owner in impl_found[:1]:
                out.append((CodeIndex.method_qname(impl_owner, impl_method), {"impl": True}))
        return out


def build_graph(files: Iterable[JavaFile]) -> Tuple[CodeIndex, List[SymbolRow], List[Edge]]:
    index = CodeIndex(files)
    symbols, edges = GraphBuilder(index).build()
    return index, symbols, edges
