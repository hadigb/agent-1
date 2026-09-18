"""Document model consumed by the renderer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..detectors.model import EndpointSpec


@dataclass
class EnumValue:
    name: str
    label: str = ""        # Persian label (from code or LLM)
    code: Optional[str] = None
    wire: str = ""         # value sent on the wire


@dataclass
class EnumTable:
    qname: str
    name: str
    title: str = ""
    values: List[EnumValue] = field(default_factory=list)


@dataclass
class FieldRow:
    name: str                      # wire name
    java_name: str
    type_str: str
    required: bool = False
    description: str = ""          # final description shown in the document
    code_description: str = ""     # deterministic description found in the code
    location: str = "body"         # header | query | path | body | form | cookie | response
    default: Optional[str] = None
    enum_qname: Optional[str] = None
    nested_qname: Optional[str] = None
    is_list: bool = False
    example: Any = None
    owner_qname: Optional[str] = None
    java_type: str = ""


@dataclass
class TypeTable:
    qname: str
    name: str
    title: str = ""
    description: str = ""
    fields: List[FieldRow] = field(default_factory=list)
    side: str = "request"          # request | response


@dataclass
class ErrorRow:
    code: str
    title: str = ""
    note: str = ""
    source: str = ""               # code | llm | config | override


@dataclass
class EndpointDoc:
    spec: EndpointSpec
    number: int = 0
    title: str = ""
    description: str = ""
    address: str = ""
    http_method: str = ""
    header_rows: List[FieldRow] = field(default_factory=list)
    request_rows: List[FieldRow] = field(default_factory=list)
    response_rows: List[FieldRow] = field(default_factory=list)
    nested_tables: List[TypeTable] = field(default_factory=list)
    enum_tables: Dict[str, EnumTable] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    success_sample: str = ""
    failure_samples: List[str] = field(default_factory=list)
    error_rows: List[ErrorRow] = field(default_factory=list)
    call_steps_html: str = ""
    type_closure: List[str] = field(default_factory=list)   # project types involved (request + response)
    request_type_name: str = ""
    response_type_name: str = ""
    analysis_status: str = "none"  # none | cached | stale | fresh
    anchor: str = ""
    auth_text: str = ""
    business_rules: List[str] = field(default_factory=list)
    failure_rows: List[FieldRow] = field(default_factory=list)

    def doc_hash_source(self) -> str:
        """Text whose hash identifies the documented content (for change-log detection)."""
        parts = [self.http_method, self.address, self.title, self.description]
        for r in self.header_rows + self.request_rows + self.response_rows:
            parts.append(f"{r.location}|{r.name}|{r.type_str}|{r.required}|{r.description}")
        for t in self.nested_tables:
            parts.append(f"T:{t.qname}:{t.description}")
            for r in t.fields:
                parts.append(f"{t.qname}|{r.name}|{r.type_str}|{r.required}|{r.description}")
        for e in self.enum_tables.values():
            parts.append("E:" + e.qname + ":" + ",".join(f"{v.name}={v.label}" for v in e.values))
        parts.extend(self.notes)
        parts.extend(f"{e.code}:{e.title}" for e in self.error_rows)
        return "\n".join(parts)
