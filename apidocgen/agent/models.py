from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FlowStep:
    number: int
    actor: str
    action: str
    is_new: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"number": self.number, "actor": self.actor, "action": self.action, "is_new": self.is_new}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "FlowStep":
        return FlowStep(number=int(d.get("number") or 0), actor=str(d.get("actor") or ""),
                        action=str(d.get("action") or ""), is_new=bool(d.get("is_new")))


@dataclass
class AlternateFlow:
    name: str
    condition: str
    steps: List[str] = field(default_factory=list)
    is_new: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "condition": self.condition, "steps": list(self.steps), "is_new": self.is_new}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "AlternateFlow":
        return AlternateFlow(name=str(d.get("name") or ""), condition=str(d.get("condition") or ""),
                             steps=[str(s) for s in (d.get("steps") or [])], is_new=bool(d.get("is_new")))


@dataclass
class UseCaseSpec:
    """Classic use-case specification produced by the senior analyst agent."""

    use_case_id: str
    name: str
    endpoint_id: str
    actors: List[str] = field(default_factory=list)
    description: str = ""
    trigger: str = ""
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)
    main_flow: List[FlowStep] = field(default_factory=list)
    alternative_flows: List[AlternateFlow] = field(default_factory=list)
    exception_flows: List[AlternateFlow] = field(default_factory=list)
    business_rules: List[str] = field(default_factory=list)
    special_requirements: List[str] = field(default_factory=list)
    related_methods: List[str] = field(default_factory=list)
    new_requirement_impacts: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "use_case_id": self.use_case_id, "name": self.name, "endpoint_id": self.endpoint_id,
            "actors": list(self.actors), "description": self.description, "trigger": self.trigger,
            "preconditions": list(self.preconditions), "postconditions": list(self.postconditions),
            "main_flow": [s.to_dict() for s in self.main_flow],
            "alternative_flows": [f.to_dict() for f in self.alternative_flows],
            "exception_flows": [f.to_dict() for f in self.exception_flows],
            "business_rules": list(self.business_rules),
            "special_requirements": list(self.special_requirements),
            "related_methods": list(self.related_methods),
            "new_requirement_impacts": list(self.new_requirement_impacts),
            "notes": list(self.notes),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any], endpoint_id: str = "") -> "UseCaseSpec":
        def steps(raw: Any) -> List[FlowStep]:
            out: List[FlowStep] = []
            for i, item in enumerate(raw or [], 1):
                if isinstance(item, str):
                    out.append(FlowStep(number=i, actor="", action=item))
                elif isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("number", i)
                    out.append(FlowStep.from_dict(item))
            return out

        return UseCaseSpec(
            use_case_id=str(d.get("use_case_id") or d.get("id") or endpoint_id),
            name=str(d.get("name") or d.get("title") or endpoint_id),
            endpoint_id=str(d.get("endpoint_id") or endpoint_id),
            actors=[str(a) for a in (d.get("actors") or [])],
            description=str(d.get("description") or ""),
            trigger=str(d.get("trigger") or ""),
            preconditions=[str(x) for x in (d.get("preconditions") or [])],
            postconditions=[str(x) for x in (d.get("postconditions") or [])],
            main_flow=steps(d.get("main_flow")),
            alternative_flows=[AlternateFlow.from_dict(x) for x in (d.get("alternative_flows") or []) if isinstance(x, dict)],
            exception_flows=[AlternateFlow.from_dict(x) for x in (d.get("exception_flows") or []) if isinstance(x, dict)],
            business_rules=[str(x) for x in (d.get("business_rules") or [])],
            special_requirements=[str(x) for x in (d.get("special_requirements") or [])],
            related_methods=[str(x) for x in (d.get("related_methods") or [])],
            new_requirement_impacts=[str(x) for x in (d.get("new_requirement_impacts") or [])],
            notes=[str(x) for x in (d.get("notes") or [])],
        )
