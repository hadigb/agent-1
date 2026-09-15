"""Detectors for code that is not annotation-driven: Servlets, Spring functional
routes (RouterFunction), user-defined custom rules and a manual endpoints file.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..graph.index import CodeIndex
from ..javaparse.model import JavaFile, MethodDecl, TypeDecl, TypeRef, find_annotation
from .common import (
    all_strs, expand_constants, first_str, is_deprecated, method_description, method_summary, normalize_path,
    param_description, required_from_annotations, unwrap_response,
)
from .model import EndpointSpec, ParamSpec

SERVLET_METHODS = {"doGet": "GET", "doPost": "POST", "doPut": "PUT", "doDelete": "DELETE", "doHead": "HEAD",
                   "doOptions": "OPTIONS", "doTrace": "TRACE", "service": "ANY"}


# ---------------------------------------------------------------------------- servlets
class ServletDetector:
    name = "servlet"

    def detect(self, t: TypeDecl, jf: JavaFile, index: CodeIndex) -> List[EndpointSpec]:
        if t.kind != "class":
            return []
        ws = find_annotation(t.annotations, "WebServlet")
        extends_servlet = any(e.simple_name in ("HttpServlet", "GenericServlet") for e in t.extends)
        if not ws and not extends_servlet:
            # maybe extends a project class that extends HttpServlet
            for anc in index.superclass_chain(t.qname)[1:]:
                at = index.types.get(anc)
                if at and any(e.simple_name == "HttpServlet" for e in at.extends):
                    extends_servlet = True
                    break
        if not ws and not extends_servlet:
            return []
        paths = all_strs(ws.get("urlPatterns", "value")) if ws else []
        paths = [str(expand_constants(p, t, index)) for p in paths] or ["/" + t.name]
        out: List[EndpointSpec] = []
        for m in t.methods:
            if m.name not in SERVLET_METHODS or m.body is None:
                continue
            http = SERVLET_METHODS[m.name]
            params: List[ParamSpec] = []
            seen: set = set()
            for call in m.body.calls:
                if call.name == "getParameter" and call.string_args:
                    n = call.string_args[0]
                    if ("query", n) not in seen:
                        seen.add(("query", n))
                        params.append(ParamSpec(name=n, location="query", java_name=n, type=TypeRef(name="String")))
                elif call.name == "getHeader" and call.string_args:
                    n = call.string_args[0]
                    if ("header", n) not in seen:
                        seen.add(("header", n))
                        params.append(ParamSpec(name=n, location="header", java_name=n, type=TypeRef(name="String")))
            class_doc = t.javadoc.text if t.javadoc else t.comment
            spec = EndpointSpec(http_method=http, path=normalize_path(paths[0]), framework="servlet",
                                handler_qname=CodeIndex.method_qname(t.qname, m), type_qname=t.qname, file_path=jf.path,
                                params=params, body_type=None, response_type=None,
                                summary=method_summary(m) or class_doc,
                                description=method_description(m) or class_doc,
                                deprecated=is_deprecated(m.annotations, m.javadoc), path_aliases=paths[1:],
                                notes=(["مسیرهای جایگزین: " + ", ".join(paths[1:])] if len(paths) > 1 else []))
            spec.id = spec.make_id()
            out.append(spec)
        return out


# ---------------------------------------------------------------------------- spring functional routes
_HTTP_FUNCS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


class SpringFunctionalDetector:
    """Detects ``RouterFunctions.route(GET("/x"), handler::m)`` and builder style ``.GET("/x", handler::m)``."""
    name = "spring-functional"

    def detect(self, t: TypeDecl, jf: JavaFile, index: CodeIndex) -> List[EndpointSpec]:
        out: List[EndpointSpec] = []
        ctx = index.context_for(t.qname)
        for m in t.methods:
            if m.body is None or m.return_type is None or m.return_type.simple_name != "RouterFunction":
                continue
            param_types = {p.name: p.type for p in m.params}
            pending_ref: Optional[str] = None
            pending_path: Optional[Tuple[str, str]] = None
            prefix = ""
            for call in m.body.calls:
                if call.name in ("path", "nest") and call.string_args and not call.method_refs:
                    prefix = call.string_args[0]  # best effort: last seen nesting prefix
                if call.name in _HTTP_FUNCS and call.string_args:
                    ref = call.method_refs[0] if call.method_refs else pending_ref
                    if ref:
                        out.extend(self._make(t, m, jf, index, ctx, call.name, normalize_path(prefix, call.string_args[0]),
                                              ref, param_types))
                        pending_ref = None
                    else:
                        pending_path = (call.name, normalize_path(prefix, call.string_args[0]))
                    continue
                if call.name in ("route", "andRoute") and call.method_refs:
                    if pending_path:
                        out.extend(self._make(t, m, jf, index, ctx, pending_path[0], pending_path[1],
                                              call.method_refs[0], param_types))
                        pending_path = None
                    else:
                        pending_ref = call.method_refs[0]
        return out

    def _make(self, t: TypeDecl, m: MethodDecl, jf: JavaFile, index: CodeIndex, ctx, http: str, path: str, ref: str,
              param_types: Dict[str, TypeRef]) -> List[EndpointSpec]:
        recv, mname = ref.split("::", 1)
        handler_type: Optional[str] = None
        if recv in param_types:
            r = index.resolve(param_types[recv].name, ctx)
            handler_type = r.qname if r.ok else None
        elif recv in m.body.locals:
            r = index.resolve(m.body.locals[recv], ctx)
            handler_type = r.qname if r.ok else None
        else:
            ft = index.field_type(t.qname, recv)
            if ft:
                r = index.resolve(ft[0].name, ctx)
                handler_type = r.qname if r.ok else None
            elif recv[:1].isupper():
                r = index.resolve(recv, ctx)
                handler_type = r.qname if r.ok else None
            elif recv == "this":
                handler_type = t.qname
        handler_q = CodeIndex.method_qname(t.qname, m)
        body_type: Optional[TypeRef] = None
        response: Optional[TypeRef] = None
        hm: Optional[MethodDecl] = None
        if handler_type:
            found = index.find_method(handler_type, mname)
            if found:
                hm, owner = found[0]
                handler_q = CodeIndex.method_qname(owner, hm)
                if hm.body:
                    for c in hm.body.calls:
                        is_request_body = (c.name in ("bodyToMono", "bodyToFlux", "toMono", "toFlux")
                                           or (c.name == "body" and c.argc == 1))
                        if is_request_body and c.class_args and body_type is None:
                            body_type = TypeRef(name=c.class_args[0])
                            if c.name in ("bodyToFlux", "toFlux"):
                                body_type = TypeRef(name="List", args=[body_type])
                        if c.name in ("body", "bodyValue") and c.argc == 2 and c.class_args:
                            response = TypeRef(name=c.class_args[-1])
        params: List[ParamSpec] = []
        if body_type is not None:
            params.append(ParamSpec(name="body", location="body", java_name="body", type=body_type, required=True))
        for pv in re.findall(r"\{([^}]+)\}", path):
            params.append(ParamSpec(name=pv, location="path", java_name=pv, type=TypeRef(name="String"), required=True))
        if hm and hm.body:
            for c in hm.body.calls:
                if c.name == "queryParam" and c.string_args:
                    params.append(ParamSpec(name=c.string_args[0], location="query", java_name=c.string_args[0],
                                            type=TypeRef(name="String")))
        spec = EndpointSpec(http_method=http, path=path, framework="spring-functional", handler_qname=handler_q,
                            type_qname=handler_type or t.qname, file_path=jf.path, params=params, body_type=body_type,
                            response_type=response, summary=method_summary(hm) if hm else "",
                            description=method_description(hm) if hm else "",
                            notes=["مسیر با RouterFunction تعریف شده است"])
        spec.id = spec.make_id()
        return [spec]


# ---------------------------------------------------------------------------- custom rules (config driven)
class CustomRuleDetector:
    """A detector configured entirely from YAML - for in-house frameworks.

    rule schema (all keys optional unless noted)::

        name: legacy                       # required
        class:
          annotation: ServiceHandler       # class carries this annotation
          extends: BaseHandler             # or any ancestor with this simple name
          implements: RequestHandler
          name_regex: ".*Handler$"
        method:
          annotation: Handles              # method carries this annotation
          name_regex: "^(handle|execute)$"
          modifiers: [public]
          skip_static: true
        http_method:
          annotation_arg: method           # read from the method annotation's arg
          class_annotation_arg: method
          default: POST
        path:
          annotation_arg: value            # from the method annotation
          class_annotation_arg: path       # prefix from the class annotation
          template: "/API/{class_name}"    # placeholders: {class_name}, {method_name}, {package}
          strip_suffix: Handler            # applied to {class_name}
        request:
          param_index: 0                   # which parameter is the body (default: first non-ignored parameter)
          param_annotation: Body
          none: false                      # true = no request body
        response:
          return_type: true                # default: the unwrapped return type
          base_type_arg: 1                 # generic argument index of the matched base type
        headers:                           # extra header params implied by the framework
          - {name: ApiKey, required: true, type: String, description: "..."}
    """
    name = "custom"

    def __init__(self, rule: Dict[str, Any]) -> None:
        self.rule = rule
        self.rule_name = rule.get("name", "custom")

    def _class_matches(self, t: TypeDecl, index: CodeIndex) -> bool:
        c = self.rule.get("class") or {}
        if not c:
            return False
        if c.get("annotation") and not any(a.simple_name == c["annotation"] for a in t.annotations):
            return False
        if c.get("name_regex") and not re.search(c["name_regex"], t.name):
            return False
        if c.get("extends") or c.get("implements"):
            wanted = {x for x in (c.get("extends"), c.get("implements")) if x}
            names = {r.simple_name for r in t.extends + t.implements}
            for anc in index.superclass_chain(t.qname)[1:]:
                names.add(anc.rsplit(".", 1)[-1])
                at = index.types.get(anc)
                if at:
                    names.update(r.simple_name for r in at.extends + at.implements)
            if not wanted & names:
                return False
        return True

    def _method_matches(self, m: MethodDecl) -> bool:
        c = self.rule.get("method") or {}
        if c.get("annotation") and not any(a.simple_name == c["annotation"] for a in m.annotations):
            return False
        if c.get("name_regex") and not re.search(c["name_regex"], m.name):
            return False
        for mod in c.get("modifiers", []) or []:
            if mod not in m.modifiers:
                return False
        if c.get("skip_static", True) and m.is_static:
            return False
        if not c:
            return m.is_public and not m.is_static
        return True

    def detect(self, t: TypeDecl, jf: JavaFile, index: CodeIndex) -> List[EndpointSpec]:
        if t.kind not in ("class", "interface") or not self._class_matches(t, index):
            return []
        out: List[EndpointSpec] = []
        c = self.rule.get("class") or {}
        class_anno = find_annotation(t.annotations, c["annotation"]) if c.get("annotation") else None
        hm_cfg = self.rule.get("http_method") or {}
        path_cfg = self.rule.get("path") or {}
        req_cfg = self.rule.get("request") or {}
        resp_cfg = self.rule.get("response") or {}
        ignored = set(self.rule.get("ignored_param_types", []) or [])
        for m in t.methods:
            if not self._method_matches(m):
                continue
            mcfg = self.rule.get("method") or {}
            manno = find_annotation(m.annotations, mcfg["annotation"]) if mcfg.get("annotation") else None
            # http method
            http = None
            if hm_cfg.get("annotation_arg") and manno:
                http = first_str(manno.get(hm_cfg["annotation_arg"]))
            if not http and hm_cfg.get("class_annotation_arg") and class_anno:
                http = first_str(class_anno.get(hm_cfg["class_annotation_arg"]))
            http = (http or hm_cfg.get("default") or "POST").rsplit(".", 1)[-1].upper()
            # path
            cls_name = t.name
            if path_cfg.get("strip_suffix") and cls_name.endswith(path_cfg["strip_suffix"]):
                cls_name = cls_name[: -len(path_cfg["strip_suffix"])]
            prefix = ""
            if path_cfg.get("class_annotation_arg") and class_anno:
                prefix = str(expand_constants(first_str(class_anno.get(path_cfg["class_annotation_arg"])) or "", t, index))
            sub = None
            if path_cfg.get("annotation_arg") and manno:
                sub = first_str(manno.get(path_cfg["annotation_arg"]))
                if sub is not None:
                    sub = str(expand_constants(sub, t, index))
            if sub is None and path_cfg.get("template"):
                sub = path_cfg["template"].format(class_name=cls_name, method_name=m.name, package=jf.package)
            if sub is None:
                sub = "" if prefix else "/" + cls_name
            path = normalize_path(prefix, sub)
            # request
            params: List[ParamSpec] = []
            body_type: Optional[TypeRef] = None
            candidates = [p for p in m.params if p.type.simple_name not in ignored]
            if not req_cfg.get("none"):
                chosen = None
                if req_cfg.get("param_annotation"):
                    chosen = next((p for p in candidates if any(a.simple_name == req_cfg["param_annotation"] for a in p.annotations)), None)
                elif "param_index" in req_cfg:
                    idx = int(req_cfg["param_index"])
                    chosen = candidates[idx] if 0 <= idx < len(candidates) else None
                elif candidates:
                    chosen = candidates[0]
                if chosen is not None:
                    body_type = chosen.type
                    params.append(ParamSpec(name=chosen.name, location="body", java_name=chosen.name, type=chosen.type,
                                            required=True, description=param_description(chosen, m.javadoc)))
            # response
            response: Optional[TypeRef] = None
            if "base_type_arg" in resp_cfg:
                idx = int(resp_cfg["base_type_arg"])
                for ref in t.extends + t.implements:
                    if ref.args and idx < len(ref.args):
                        response = ref.args[idx]
                        break
            if response is None and resp_cfg.get("return_type", True):
                response = unwrap_response(m.return_type)
            for h in self.rule.get("headers", []) or []:
                params.append(ParamSpec(name=h["name"], location="header", java_name=h["name"],
                                        type=TypeRef(name=h.get("type", "String")), required=bool(h.get("required", False)),
                                        description=h.get("description", "")))
            class_doc = t.javadoc.text if t.javadoc else t.comment
            spec = EndpointSpec(http_method=http, path=path, framework=f"custom:{self.rule_name}",
                                handler_qname=CodeIndex.method_qname(t.qname, m), type_qname=t.qname, file_path=jf.path,
                                params=params, body_type=body_type, response_type=response,
                                summary=method_summary(m) or class_doc,
                                description=method_description(m) or class_doc,
                                deprecated=is_deprecated(m.annotations, m.javadoc))
            spec.id = spec.make_id()
            out.append(spec)
        return out


# ---------------------------------------------------------------------------- manual endpoints file
class ManualEndpointsDetector:
    """Endpoints listed by hand (or by ``apidocgen discover``) in a YAML file::

        endpoints:
          - handler: com.x.LegacyHandler#execute        # method qname; the parameter list may be omitted
            method: POST
            path: /API/Legacy
            body: com.x.LegacyRequest                    # optional (default: first parameter)
            response: com.x.LegacyResponse               # optional (default: unwrapped return type)
            summary: "..."                               # optional
            headers: [{name: ApiKey, required: true}]    # optional
    """
    name = "manual"

    def __init__(self, entries: List[Dict[str, Any]]) -> None:
        self.by_type: Dict[str, List[Dict[str, Any]]] = {}
        for e in entries or []:
            h = e.get("handler", "")
            tq = h.split("#", 1)[0]
            self.by_type.setdefault(tq, []).append(e)

    def detect(self, t: TypeDecl, jf: JavaFile, index: CodeIndex) -> List[EndpointSpec]:
        entries = self.by_type.get(t.qname)
        if not entries:
            return []
        out: List[EndpointSpec] = []
        ctx = index.context_for(t.qname)
        for e in entries:
            h = e["handler"]
            mpart = h.split("#", 1)[1] if "#" in h else ""
            mname = mpart.split("(", 1)[0]
            method = None
            for m in t.methods:
                if m.name == mname and (("(" not in mpart) or m.signature_key() == mpart):
                    method = m
                    break
            if method is None:
                continue
            body_type: Optional[TypeRef] = None
            if e.get("body"):
                body_type = TypeRef(name=str(e["body"]))
            elif method.params:
                body_type = method.params[0].type
            response = TypeRef(name=str(e["response"])) if e.get("response") else unwrap_response(method.return_type)
            params: List[ParamSpec] = []
            if body_type is not None:
                params.append(ParamSpec(name=method.params[0].name if method.params else "body", location="body",
                                        java_name=method.params[0].name if method.params else "body", type=body_type,
                                        required=True))
            for hdr in e.get("headers", []) or []:
                params.append(ParamSpec(name=hdr["name"], location="header", java_name=hdr["name"],
                                        type=TypeRef(name=hdr.get("type", "String")), required=bool(hdr.get("required")),
                                        description=hdr.get("description", "")))
            for q in e.get("query", []) or []:
                params.append(ParamSpec(name=q["name"], location="query", java_name=q["name"],
                                        type=TypeRef(name=q.get("type", "String")), required=bool(q.get("required")),
                                        description=q.get("description", "")))
            spec = EndpointSpec(http_method=str(e.get("method", "POST")).upper(), path=normalize_path(str(e.get("path", "/" + t.name))),
                                framework="manual", handler_qname=CodeIndex.method_qname(t.qname, method),
                                type_qname=t.qname, file_path=jf.path, params=params, body_type=body_type,
                                response_type=response, summary=e.get("summary") or method_summary(method),
                                description=e.get("description") or method_description(method))
            spec.id = spec.make_id()
            out.append(spec)
        return out
