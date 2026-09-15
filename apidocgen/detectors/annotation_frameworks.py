"""Annotation-driven endpoint detection for Spring MVC / WebFlux, JAX-RS
(Jersey, RESTEasy, Quarkus) and Micronaut.

All three frameworks follow the same shape (class-level base path, method-level
HTTP mapping, parameter-binding annotations) so they are expressed as
*profiles* over one detector implementation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..graph.index import CodeIndex
from ..javaparse.model import Annotation, JavaFile, MethodDecl, Param, TypeDecl, TypeRef, find_annotation
from .common import (
    all_strs, constant_name, expand_constants, first_str, is_deprecated, is_simple_type, media_types,
    method_description, method_summary, normalize_path, param_description, required_from_annotations,
    unwrap_response,
)
from .model import EndpointSpec, ParamSpec

IGNORED_PARAM_TYPES = {
    "HttpServletRequest", "HttpServletResponse", "ServletRequest", "ServletResponse", "HttpSession", "Principal",
    "Authentication", "Model", "ModelMap", "ModelAndView", "BindingResult", "Errors", "Locale", "TimeZone", "ZoneId",
    "WebRequest", "NativeWebRequest", "UriComponentsBuilder", "RedirectAttributes", "SessionStatus", "InputStream",
    "Reader", "OutputStream", "Writer", "HttpMethod", "ServerWebExchange", "ServerHttpRequest", "ServerHttpResponse",
    "UriInfo", "HttpHeaders", "SecurityContext", "Request", "AsyncResponse", "Sse", "SseEventSink", "HttpRequest",
    "ContainerRequestContext", "Continuation", "UserDetails", "OAuth2User", "Jwt",
}
IGNORED_PARAM_ANNOTATIONS = {"AuthenticationPrincipal", "Context", "Suspended", "CurrentUser", "SessionAttribute",
                             "RequestAttribute", "Nullable"}


@dataclass
class Profile:
    name: str
    class_markers: Set[str]                       # annotations that make a class a controller (may be empty)
    class_path_annotations: Dict[str, List[str]]  # annotation -> arg names holding the base path
    method_mappings: Dict[str, Optional[str]]     # annotation -> HTTP method (None: read from `method` arg)
    mapping_path_args: List[str]
    method_arg_names: List[str]                   # arg names holding RequestMethod constants
    param_annotations: Dict[str, str]             # annotation -> location
    name_args: List[str]
    default_unannotated: str                      # query | body | auto
    require_class_marker: bool
    exclude_class_annotations: Set[str] = field(default_factory=set)
    consumes_args: List[str] = field(default_factory=lambda: ["consumes"])
    produces_args: List[str] = field(default_factory=lambda: ["produces"])
    class_consumes_annotations: Dict[str, str] = field(default_factory=dict)   # annotation -> consumes|produces


SPRING = Profile(
    name="spring",
    class_markers={"RestController", "Controller"},
    class_path_annotations={"RequestMapping": ["value", "path"]},
    method_mappings={"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
                     "PatchMapping": "PATCH", "RequestMapping": None},
    mapping_path_args=["value", "path"],
    method_arg_names=["method"],
    param_annotations={"RequestBody": "body", "RequestParam": "query", "PathVariable": "path", "RequestHeader": "header",
                       "CookieValue": "cookie", "RequestPart": "form", "ModelAttribute": "query-object",
                       "MatrixVariable": "query"},
    name_args=["value", "name"],
    default_unannotated="auto",
    require_class_marker=False,
    exclude_class_annotations={"FeignClient", "HttpExchange"},
)

JAXRS = Profile(
    name="jaxrs",
    class_markers=set(),
    class_path_annotations={"Path": ["value"]},
    method_mappings={"GET": "GET", "POST": "POST", "PUT": "PUT", "DELETE": "DELETE", "PATCH": "PATCH", "HEAD": "HEAD",
                     "OPTIONS": "OPTIONS", "HttpMethod": None},
    mapping_path_args=["value"],
    method_arg_names=["value"],
    param_annotations={"QueryParam": "query", "PathParam": "path", "HeaderParam": "header", "FormParam": "form",
                       "CookieParam": "cookie", "MatrixParam": "query", "BeanParam": "query-object",
                       "RestQuery": "query", "RestPath": "path", "RestHeader": "header", "RestForm": "form",
                       "RestCookie": "cookie"},
    name_args=["value"],
    default_unannotated="body",
    require_class_marker=False,
    class_consumes_annotations={"Consumes": "consumes", "Produces": "produces"},
)

MICRONAUT = Profile(
    name="micronaut",
    class_markers={"Controller"},
    class_path_annotations={"Controller": ["value", "uri"]},
    method_mappings={"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "Head": "HEAD",
                     "Options": "OPTIONS", "Trace": "TRACE"},
    mapping_path_args=["value", "uri", "uris"],
    method_arg_names=[],
    param_annotations={"Body": "body", "QueryValue": "query", "PathVariable": "path", "Header": "header",
                       "CookieValue": "cookie", "Part": "form"},
    name_args=["value", "name"],
    default_unannotated="auto",
    require_class_marker=True,
)

PROFILES: Dict[str, Profile] = {"spring": SPRING, "jaxrs": JAXRS, "micronaut": MICRONAUT}


class AnnotationFrameworkDetector:
    def __init__(self, profile: Profile, response_wrappers: Optional[List[str]] = None,
                 unannotated_object_as_body: bool = False) -> None:
        self.profile = profile
        self.wrappers = response_wrappers
        self.unannotated_object_as_body = unannotated_object_as_body

    # ------------------------------------------------------------------ class level
    def detect(self, t: TypeDecl, jf: JavaFile, index: CodeIndex) -> List[EndpointSpec]:
        p = self.profile
        if t.kind in ("enum", "annotation", "record"):
            return []
        if any(a.simple_name in p.exclude_class_annotations for a in t.annotations):
            return []
        has_marker = any(a.simple_name in p.class_markers for a in t.annotations)
        if p.require_class_marker and not has_marker:
            return []
        mapped = self._mapped_methods(t, index, has_marker)
        if not mapped:
            return []
        if p.name == "spring" and not has_marker and t.kind != "interface" and "abstract" in t.modifiers:
            # Spring registers the concrete subclass, not the abstract base.
            if self._has_annotated_subtype(t, index):
                return []
        base_paths = self._class_paths(t, index)
        class_consumes: List[str] = []
        class_produces: List[str] = []
        for a in t.annotations:
            if a.simple_name in p.class_path_annotations:
                class_consumes += media_types(a.get(*p.consumes_args))
                class_produces += media_types(a.get(*p.produces_args))
            kind = p.class_consumes_annotations.get(a.simple_name)
            if kind == "consumes":
                class_consumes += media_types(a.get("value"))
            elif kind == "produces":
                class_produces += media_types(a.get("value"))
        out: List[EndpointSpec] = []
        for owner, m in mapped:
            out.extend(self._detect_method(t, m, jf, index, base_paths, class_consumes, class_produces, owner=owner))
        return out

    def _has_annotated_subtype(self, t: TypeDecl, index: CodeIndex) -> bool:
        markers = self.profile.class_markers
        if not markers:
            return False
        for q in index.implementations(t.qname):
            st = index.types.get(q)
            if st and any(a.simple_name in markers for a in st.annotations):
                return True
        return False

    def _mapped_methods(self, t: TypeDecl, index: CodeIndex, has_marker: bool) -> List[tuple]:
        p = self.profile

        def is_mapped(m: MethodDecl) -> bool:
            return any(a.simple_name in p.method_mappings for a in m.annotations)

        own = [(t, m) for m in t.methods if is_mapped(m)]
        if p.name != "spring" or not (has_marker or t.kind == "interface"):
            return own
        seen = {(m.name, len(m.params)) for _, m in own}
        inherited: List[tuple] = []
        for owner_q in index.superclass_chain(t.qname)[1:]:
            ot = index.types.get(owner_q)
            if ot is None:
                continue
            for m in ot.methods:
                if not is_mapped(m):
                    continue
                key = (m.name, len(m.params))
                if key in seen:
                    continue
                seen.add(key)
                inherited.append((ot, m))
        return own + inherited

    def _class_paths(self, t: TypeDecl, index: Optional[CodeIndex] = None) -> List[str]:
        p = self.profile
        for a in t.annotations:
            if a.simple_name in p.class_path_annotations:
                vals = all_strs(a.get(*p.class_path_annotations[a.simple_name]))
                vals = [str(expand_constants(v, t, index)) for v in vals]
                if vals:
                    return vals
        return [""]

    def _inherit_bindings(self, child: TypeDecl, ancestor: TypeDecl, index: CodeIndex) -> Dict[str, TypeRef]:
        if child.qname == ancestor.qname:
            return {}
        ctx = index.context_for(child.qname)
        for ref in child.extends + child.implements:
            res = index.resolve(ref.name, ctx)
            if not res.ok or res.qname not in index.types:
                continue
            parent = index.types[res.qname]
            direct = {tp: arg for tp, arg in zip(parent.type_params, ref.args)}
            if res.qname == ancestor.qname:
                return direct
            inner = self._inherit_bindings(parent, ancestor, index)
            if inner:
                from ..docmodel.fields import substitute
                return {k: substitute(v, direct) for k, v in inner.items()}
        return {}

    # ------------------------------------------------------------------ method level
    def _detect_method(self, t: TypeDecl, m: MethodDecl, jf: JavaFile, index: CodeIndex, base_paths: List[str],
                       class_consumes: List[str], class_produces: List[str],
                       owner: Optional[TypeDecl] = None) -> List[EndpointSpec]:
        p = self.profile
        owner = owner or t
        mapping = next(a for a in m.annotations if a.simple_name in p.method_mappings)
        http = p.method_mappings[mapping.simple_name]
        notes: List[str] = []
        if http is None:
            methods = [constant_name(x) for x in all_strs(mapping.get(*p.method_arg_names))]
            methods = [x.upper() for x in methods if x]
            if p.name == "jaxrs" and mapping.simple_name == "HttpMethod":
                http_methods = [methods[0] if methods else "ANY"]
            else:
                http_methods = methods or ["ANY"]
        else:
            http_methods = [http]
        paths = all_strs(mapping.get(*p.mapping_path_args))
        paths = [str(expand_constants(v, owner, index)) for v in paths] or [""]
        if p.name == "jaxrs":
            pa = find_annotation(m.annotations, "Path")
            paths = [str(expand_constants(pa.get_str("value"), owner, index))] if pa and pa.get_str("value") else [""]
        full_paths = [normalize_path(b, s) for b in base_paths for s in paths]
        if len(full_paths) > 1:
            notes.append("مسیرهای جایگزین: " + ", ".join(full_paths[1:]))
        consumes = media_types(mapping.get(*p.consumes_args)) or class_consumes
        produces = media_types(mapping.get(*p.produces_args)) or class_produces
        for a in m.annotations:
            kind = p.class_consumes_annotations.get(a.simple_name)
            if kind == "consumes":
                consumes = media_types(a.get("value"))
            elif kind == "produces":
                produces = media_types(a.get("value"))
        bindings = self._inherit_bindings(t, owner, index) if owner.qname != t.qname else {}
        work_method = self._substitute_method(m, bindings) if bindings else m
        params, body_type = self._params(t, work_method, full_paths[0], index)
        response = unwrap_response(work_method.return_type, self.wrappers or None) if self.wrappers else unwrap_response(work_method.return_type)
        handler_q = CodeIndex.method_qname(owner.qname, m)
        impl_q: Optional[str] = None
        if m.body is None and owner.kind == "interface":
            for impl in index.implementations(owner.qname):
                found = index.find_method(impl, m.name, len(m.params), include_impls=False)
                for im, impl_owner in found:
                    if impl_owner == impl and im.body is not None:
                        impl_q = CodeIndex.method_qname(impl_owner, im)
                        break
                if impl_q:
                    break
        elif owner.qname != t.qname:
            impl_q = CodeIndex.method_qname(owner.qname, m)
            handler_q = CodeIndex.method_qname(t.qname, m) if any(x.name == m.name for x in t.methods) else handler_q
        specs: List[EndpointSpec] = []
        for http_m in http_methods:
            spec = EndpointSpec(http_method=http_m, path=full_paths[0], framework=p.name, handler_qname=handler_q,
                                type_qname=t.qname, file_path=jf.path, params=params, body_type=body_type,
                                response_type=response, consumes=consumes, produces=produces,
                                summary=method_summary(m), description=method_description(m),
                                deprecated=is_deprecated(m.annotations, m.javadoc) or is_deprecated(t.annotations, t.javadoc),
                                impl_qname=impl_q, notes=list(notes), path_aliases=full_paths[1:])
            spec.id = spec.make_id()
            specs.append(spec)
        return specs

    @staticmethod
    def _substitute_method(m: MethodDecl, bindings: Dict[str, TypeRef]) -> MethodDecl:
        if not bindings:
            return m
        from ..docmodel.fields import substitute
        params = []
        for p in m.params:
            np = Param(name=p.name, type=substitute(p.type, bindings), annotations=p.annotations,
                       varargs=p.varargs, modifiers=p.modifiers)
            params.append(np)
        return MethodDecl(name=m.name, return_type=substitute(m.return_type, bindings) if m.return_type else None,
                          params=params, modifiers=m.modifiers, annotations=m.annotations, javadoc=m.javadoc,
                          comment=m.comment, throws=m.throws, type_params=m.type_params, body=m.body,
                          is_constructor=m.is_constructor, start_line=m.start_line, end_line=m.end_line,
                          source=m.source, signature_source=m.signature_source)

    def _params(self, t: TypeDecl, m: MethodDecl, path: str, index: CodeIndex):
        p = self.profile
        params: List[ParamSpec] = []
        body_type: Optional[TypeRef] = None
        path_vars = set(re.findall(r"\{([^}]+)\}", path))
        for prm in m.params:
            ann_names = [a.simple_name for a in prm.annotations]
            if any(n in IGNORED_PARAM_ANNOTATIONS for n in ann_names):
                continue
            if prm.type.simple_name in IGNORED_PARAM_TYPES:
                continue
            desc = param_description(prm, m.javadoc)
            binding: Optional[Annotation] = next((a for a in prm.annotations if a.simple_name in p.param_annotations), None)
            if binding is not None:
                loc = p.param_annotations[binding.simple_name]
                name = binding.get_str(*p.name_args) or prm.name
                if prm.type.simple_name in ("Map", "MultiValueMap", "HttpHeaders", "Properties") and loc in ("query", "header", "path", "form", "cookie"):
                    if loc == "query":
                        params.append(ParamSpec(name=prm.name, location="query-object", java_name=prm.name, type=prm.type,
                                                required=False, description=desc or "همه پارامترهای درخواست", annotations=ann_names))
                    continue  # a Map of all headers / path variables is not a documented parameter
                if binding.simple_name == "Body" and binding.get_str("value"):
                    # Micronaut @Body("part") binds a body property
                    loc = "body-field"
                default = binding.get_str("defaultValue")
                dv = find_annotation(prm.annotations, "DefaultValue")
                if dv:
                    default = dv.get_str("value")
                req_explicit = binding.get("required")
                req = required_from_annotations(prm.annotations, prm.type)
                if isinstance(req_explicit, bool):
                    required = req_explicit
                elif default is not None:
                    required = False          # a default value makes the parameter optional even for primitives
                elif req is not None:
                    required = req
                elif loc == "path":
                    required = True
                elif p.name == "spring" and loc in ("query", "header", "body", "cookie", "form"):
                    required = default is None   # Spring defaults required=true unless a default value exists
                else:
                    required = False
                if loc == "body":
                    body_type = prm.type
                    params.append(ParamSpec(name=name, location="body", java_name=prm.name, type=prm.type,
                                            required=required, description=desc, annotations=ann_names))
                    continue
                params.append(ParamSpec(name=name, location=loc, java_name=prm.name, type=prm.type, required=required,
                                        default=default, description=desc, annotations=ann_names))
                continue
            # unannotated parameter
            if prm.type.simple_name in ("HttpEntity", "RequestEntity") and prm.type.args:
                body_type = prm.type.args[0]
                params.append(ParamSpec(name=prm.name, location="body", java_name=prm.name, type=body_type, required=True,
                                        description=desc, annotations=ann_names))
                continue
            req = required_from_annotations(prm.annotations, prm.type)
            if prm.name in path_vars:
                params.append(ParamSpec(name=prm.name, location="path", java_name=prm.name, type=prm.type, required=True,
                                        description=desc, annotations=ann_names))
                continue
            if p.default_unannotated == "body":
                body_type = prm.type
                params.append(ParamSpec(name=prm.name, location="body", java_name=prm.name, type=prm.type,
                                        required=True if req is None else req, description=desc, annotations=ann_names))
                continue
            # auto (Spring / Micronaut): simple types are query params, objects are bound from the query string
            # (Spring @ModelAttribute) or the body, depending on configuration.
            if is_simple_type(prm.type) or prm.type.simple_name in ("List", "Set") and prm.type.args and is_simple_type(prm.type.args[0]):
                params.append(ParamSpec(name=prm.name, location="query", java_name=prm.name, type=prm.type,
                                        required=bool(req), description=desc, annotations=ann_names))
                continue
            if prm.type.simple_name in ("Map", "MultiValueMap"):
                params.append(ParamSpec(name=prm.name, location="query-object", java_name=prm.name, type=prm.type,
                                        required=False, description=desc or "همه پارامترهای درخواست", annotations=ann_names))
                continue
            if prm.type.simple_name == "MultipartFile":
                params.append(ParamSpec(name=prm.name, location="form", java_name=prm.name, type=prm.type,
                                        required=bool(req), description=desc, annotations=ann_names))
                continue
            if self.unannotated_object_as_body or p.name == "micronaut":
                body_type = prm.type
                params.append(ParamSpec(name=prm.name, location="body", java_name=prm.name, type=prm.type,
                                        required=True if req is None else req, description=desc, annotations=ann_names))
            else:
                params.append(ParamSpec(name=prm.name, location="query-object", java_name=prm.name, type=prm.type,
                                        required=bool(req), description=desc, annotations=ann_names))
        return params, body_type
