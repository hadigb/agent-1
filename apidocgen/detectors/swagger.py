"""Swagger / OpenAPI helpers: wire names, response types, and security."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..javaparse.model import Annotation, MethodDecl, TypeDecl, TypeRef, find_annotation
from .common import all_strs

OPAQUE_RESPONSE_TYPES = {
    "Response", "HttpServletResponse", "ServletResponse", "ServerHttpResponse",
    "ServerWebExchange", "Object", "JsonNode", "HttpEntity",
}


def class_name_from_value(value: Any) -> Optional[str]:
    """Turn ``Foo.class`` / nested ``@Schema(implementation=Foo.class)`` into ``Foo``."""
    if value is None:
        return None
    if isinstance(value, dict):
        nested = value.get("implementation") or value.get("response") or value.get("schema")
        if nested is None and value.get("@"):
            nested = value.get("value")
        return class_name_from_value(nested)
    if isinstance(value, list):
        for item in value:
            name = class_name_from_value(item)
            if name:
                return name
        return None
    text = str(value).strip()
    if text.endswith(".class"):
        text = text[:-6]
    text = text.rsplit(".", 1)[-1] if text else ""
    if not text or text in ("Void", "void", "None"):
        return None
    return text


def swagger_response_type(method: MethodDecl) -> Optional[TypeRef]:
    """Response DTO declared by Swagger/OpenAPI annotations when the Java return type is opaque."""
    operation = find_annotation(method.annotations, "ApiOperation")
    if operation is not None:
        name = class_name_from_value(operation.get("response"))
        if name:
            return TypeRef(name=name)
    schema = find_annotation(method.annotations, "Schema")
    if schema is not None:
        name = class_name_from_value(schema.get("implementation") or schema.get("name"))
        if name and name[0:1].isupper() and name not in OPAQUE_RESPONSE_TYPES:
            return TypeRef(name=name)
    for annotation in method.annotations:
        if annotation.simple_name not in ("ApiResponse", "ApiResponses", "ApiReturn"):
            continue
        found = _response_from_api_response(annotation)
        if found is not None:
            return found
    return None


def _response_from_api_response(annotation: Annotation) -> Optional[TypeRef]:
    if annotation.simple_name == "ApiResponses":
        for item in annotation.get_list("value"):
            if isinstance(item, dict):
                fake = Annotation(name=str(item.get("@") or "ApiResponse"), args=item)
                found = _response_from_api_response(fake)
                if found is not None:
                    return found
        return None
    code = str(annotation.get("code") or annotation.get("responseCode") or "200")
    if code not in ("200", "201", "OK", "CREATED"):
        name = class_name_from_value(annotation.get("response") or annotation.get("implementation"))
        if name:
            return None  # error payloads are handled separately
    for key in ("response", "implementation", "content", "schema"):
        name = class_name_from_value(annotation.get(key))
        if name and name not in OPAQUE_RESPONSE_TYPES:
            return TypeRef(name=name)
    return None


def extract_auth(type_decl: TypeDecl, method: MethodDecl) -> Dict[str, Any]:
    """Security annotations and auth-related headers on the handler."""
    roles: List[str] = []
    notes: List[str] = []
    scheme = ""
    headers: List[str] = []
    for annotation in list(type_decl.annotations) + list(method.annotations):
        name = annotation.simple_name
        if name == "PermitAll":
            scheme = scheme or "none"
        elif name in ("PreAuthorize", "PostAuthorize"):
            expr = annotation.get_str("value") or ""
            if expr:
                notes.append(expr)
            scheme = scheme or "preauthorize"
        elif name == "Secured":
            roles.extend(all_strs(annotation.get("value")))
            scheme = scheme or "roles"
        elif name == "RolesAllowed":
            roles.extend(all_strs(annotation.get("value")))
            scheme = scheme or "roles"
        elif name in ("SecurityRequirement", "ApiSecurity"):
            scheme = annotation.get_str("name") or scheme or "openapi"
            roles.extend(all_strs(annotation.get("scopes")))
        elif name in ("BearerAuth", "ApiKeyAuth", "BasicAuth"):
            scheme = scheme or name.replace("Auth", "").lower()
    for param in method.params:
        header = find_annotation(param.annotations, "RequestHeader", "HeaderParam", "Header")
        if header is None:
            continue
        header_name = header.get_str("value", "name") or param.name
        if header_name.lower() in ("authorization", "accesstoken", "access-token", "token", "bearer", "apikey", "api-key"):
            headers.append(header_name)
            if not scheme:
                scheme = "header"
    return {
        "scheme": scheme,
        "roles": [str(role) for role in roles if role],
        "note": " ؛ ".join(note for note in notes if note),
        "headers": headers,
    }


def auth_description(auth: Dict[str, Any]) -> str:
    if not auth:
        return ""
    scheme = (auth.get("scheme") or "").lower()
    roles = auth.get("roles") or []
    note = auth.get("note") or ""
    headers = auth.get("headers") or []
    if scheme in ("", "none") and not roles and not note and not headers:
        return ""
    labels = {
        "preauthorize": "Spring Security (PreAuthorize)",
        "roles": "نقش‌های مجاز",
        "bearer": "Bearer Token",
        "basic": "Basic Authentication",
        "header": "هدر احراز هویت",
        "openapi": "OpenAPI Security",
        "none": "بدون احراز هویت",
    }
    parts = [labels.get(scheme, scheme or "احراز هویت")]
    if roles:
        parts.append("نقش‌ها: " + ", ".join(roles))
    if headers:
        parts.append("هدر: " + ", ".join(headers))
    if note:
        parts.append(note)
    return " — ".join(p for p in parts if p)
