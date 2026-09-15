"""Endpoint specification produced by the detectors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..javaparse.model import TypeRef


@dataclass
class ParamSpec:
    name: str                       # name on the wire
    location: str                   # header | query | path | body | form | cookie | query-object
    java_name: str
    type: TypeRef
    required: bool = False
    default: Optional[str] = None
    description: str = ""           # deterministic description found in code (javadoc / annotations)
    annotations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "location": self.location, "java_name": self.java_name,
            "type": self.type.to_dict(), "required": self.required, "default": self.default,
            "description": self.description, "annotations": self.annotations,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ParamSpec":
        return ParamSpec(name=d["name"], location=d["location"], java_name=d.get("java_name", d["name"]),
                         type=TypeRef.from_dict(d["type"]), required=d.get("required", False),
                         default=d.get("default"), description=d.get("description", ""),
                         annotations=d.get("annotations", []))


@dataclass
class EndpointSpec:
    http_method: str
    path: str
    framework: str
    handler_qname: str              # method symbol qname (where the mapping was found)
    type_qname: str
    file_path: str
    params: List[ParamSpec] = field(default_factory=list)
    body_type: Optional[TypeRef] = None
    response_type: Optional[TypeRef] = None
    consumes: List[str] = field(default_factory=list)
    produces: List[str] = field(default_factory=list)
    summary: str = ""               # from OpenAPI/Swagger annotations or javadoc first sentence
    description: str = ""           # javadoc text
    deprecated: bool = False
    impl_qname: Optional[str] = None   # implementing method when the mapping sits on an interface
    notes: List[str] = field(default_factory=list)
    path_aliases: List[str] = field(default_factory=list)
    id: str = ""

    def make_id(self) -> str:
        return f"{self.http_method} {self.path}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "http_method": self.http_method, "path": self.path, "framework": self.framework,
            "handler_qname": self.handler_qname, "type_qname": self.type_qname, "file_path": self.file_path,
            "params": [p.to_dict() for p in self.params],
            "body_type": self.body_type.to_dict() if self.body_type else None,
            "response_type": self.response_type.to_dict() if self.response_type else None,
            "consumes": self.consumes, "produces": self.produces, "summary": self.summary,
            "description": self.description, "deprecated": self.deprecated, "impl_qname": self.impl_qname,
            "notes": self.notes, "path_aliases": self.path_aliases,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EndpointSpec":
        e = EndpointSpec(http_method=d["http_method"], path=d["path"], framework=d["framework"],
                         handler_qname=d["handler_qname"], type_qname=d["type_qname"], file_path=d["file_path"],
                         params=[ParamSpec.from_dict(p) for p in d.get("params", [])],
                         body_type=TypeRef.from_dict(d["body_type"]) if d.get("body_type") else None,
                         response_type=TypeRef.from_dict(d["response_type"]) if d.get("response_type") else None,
                         consumes=d.get("consumes", []), produces=d.get("produces", []), summary=d.get("summary", ""),
                         description=d.get("description", ""), deprecated=d.get("deprecated", False),
                         impl_qname=d.get("impl_qname"), notes=d.get("notes", []), path_aliases=d.get("path_aliases", []))
        e.id = d.get("id", "") or e.make_id()
        return e
