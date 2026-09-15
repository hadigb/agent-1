"""Endpoint detector registry."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..graph.index import CodeIndex
from .annotation_frameworks import PROFILES, AnnotationFrameworkDetector
from .model import EndpointSpec, ParamSpec  # noqa: F401
from .others import CustomRuleDetector, ManualEndpointsDetector, ServletDetector, SpringFunctionalDetector

# One entry per switchable, config-driven detector: the config flag that turns
# it on (with its default), and a factory that builds the detector instance(s)
# for it. build_detectors() then just walks this table instead of repeating
# an "if det_cfg.get(flag, default): dets.append(...)" per framework.
_DetectorFactory = Callable[[Dict[str, Any], Any], List[Any]]


def _build_spring(det_cfg: Dict[str, Any], wrappers: Any) -> List[Any]:
    unannotated_as_body = bool(det_cfg.get("spring_unannotated_object_as_body", False))
    return [
        AnnotationFrameworkDetector(PROFILES["spring"], wrappers, unannotated_object_as_body=unannotated_as_body),
        SpringFunctionalDetector(),
    ]


def _build_jaxrs(_det_cfg: Dict[str, Any], wrappers: Any) -> List[Any]:
    return [AnnotationFrameworkDetector(PROFILES["jaxrs"], wrappers)]


def _build_micronaut(_det_cfg: Dict[str, Any], wrappers: Any) -> List[Any]:
    return [AnnotationFrameworkDetector(PROFILES["micronaut"], wrappers)]


def _build_servlet(_det_cfg: Dict[str, Any], _wrappers: Any) -> List[Any]:
    return [ServletDetector()]


_SWITCHABLE_DETECTORS: List[tuple[str, bool, _DetectorFactory]] = [
    ("spring", True, _build_spring),
    ("jaxrs", True, _build_jaxrs),
    ("micronaut", False, _build_micronaut),
    ("servlet", True, _build_servlet),
]


def build_detectors(cfg: Dict[str, Any], manual_entries: Optional[List[Dict[str, Any]]] = None) -> list:
    det_cfg = cfg.get("detectors", {}) or {}
    analysis_cfg = cfg.get("analysis", {}) or {}
    wrappers = analysis_cfg.get("response_wrappers")

    dets: list = []
    for flag, default_enabled, factory in _SWITCHABLE_DETECTORS:
        if det_cfg.get(flag, default_enabled):
            dets.extend(factory(det_cfg, wrappers))
    for rule in det_cfg.get("custom", []) or []:
        dets.append(CustomRuleDetector(rule))
    if manual_entries:
        dets.append(ManualEndpointsDetector(manual_entries))
    return dets


def detect_endpoints(index: CodeIndex, detectors: list) -> List[EndpointSpec]:
    specs: List[EndpointSpec] = []
    for qname, t in index.types.items():
        jf = index.type_file[qname]
        for d in detectors:
            try:
                specs.extend(d.detect(t, jf, index))
            except Exception as e:  # pragma: no cover - a detector bug must not kill the scan
                specs_err = EndpointSpec(http_method="?", path="?", framework=getattr(d, "name", "?"),
                                         handler_qname=qname, type_qname=qname, file_path=jf.path,
                                         notes=[f"detector error: {e!r}"])
                specs_err.id = f"ERROR {qname} {getattr(d, 'name', '?')}"
                specs.append(specs_err)
    # de-duplicate: same handler + same method + same path (e.g. interface + implementation both annotated)
    seen: Dict[str, EndpointSpec] = {}
    out: List[EndpointSpec] = []
    for s in specs:
        key = f"{s.http_method} {s.path} {s.handler_qname}"
        if key in seen:
            continue
        seen[key] = s
        out.append(s)
    # make ids unique
    counts: Dict[str, int] = {}
    for s in out:
        counts[s.id] = counts.get(s.id, 0) + 1
    used: Dict[str, int] = {}
    for s in out:
        if counts[s.id] > 1:
            used[s.id] = used.get(s.id, 0) + 1
            s.id = f"{s.id} @{s.handler_qname.split('#')[0].rsplit('.', 1)[-1]}.{s.handler_qname.split('#')[1].split('(')[0]}"
    return out
