"""LLM-assisted endpoint discovery for code that uses no known framework
annotations (in-house dispatchers, reflection based routing ...).

Only *skeletons* (class header + method signatures, no bodies) are sent, so
this is cheap; results are cached by skeleton hash and written to the manual
endpoints file for human review.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

import yaml

from ..graph.index import CodeIndex
from ..llm.base import LLMClient, LLMError, extract_json
from ..project import Project
from ..util.tokens import estimate_tokens
from .units import PROMPT_VERSION, cache_key, content_hash, normalize

DISCOVER_SYSTEM = """You are an expert Java/HTTP integration engineer. You are given skeletons (class headers and method
signatures, no bodies) of Java classes from a service that exposes HTTP/REST endpoints through an in-house or unusual
framework. Decide which public methods are externally callable HTTP endpoints and infer, from names, annotations,
base classes and parameter/return types, the HTTP method and the request path.

Return ONLY a JSON object:
{"endpoints": [ {"handler": "<fully.qualified.Class#methodName>", "method": "POST", "path": "/Api/Name",
                 "body": "<request parameter type or empty>", "summary": "<short English or Persian summary>",
                 "confidence": 0.0-1.0 } ]}
Skip framework plumbing, DTO accessors, constructors and helpers. When the path cannot be inferred, derive it from the
class name (e.g. ReverseHandler -> /Reverse). Prefer precision over recall.
"""


def class_skeleton(index: CodeIndex, qname: str) -> str:
    t = index.types[qname]
    lines = [normalize(t.header_source) + " {"]
    for m in t.methods:
        if not m.is_public and "protected" not in m.modifiers:
            continue
        lines.append("    " + normalize(m.signature_source or m.name) + ";")
    lines.append("}")
    return "\n".join(lines)


def discover_endpoints(project: Project, client: LLMClient, class_regex: str = r".*(Handler|Service|Resource|Api|Endpoint|Controller|Processor|Action)$",
                       batch_tokens: int = 6000, progress: Optional[Callable[[str], None]] = None,
                       min_confidence: float = 0.5) -> List[Dict[str, Any]]:
    log = progress or (lambda m: None)
    index = project.index
    known = {e.handler_qname.split("#", 1)[0] for e in project.endpoints()}
    pat = re.compile(class_regex)
    candidates = [q for q, t in index.types.items() if t.kind == "class" and pat.match(t.name) and q not in known
                  and any(m.is_public or "protected" in m.modifiers for m in t.methods)]
    log(f"{len(candidates)} candidate classes")
    found: List[Dict[str, Any]] = []
    batch: List[str] = []
    batch_tok = 0

    def flush() -> None:
        nonlocal batch, batch_tok
        if not batch:
            return
        skeletons = {q: class_skeleton(index, q) for q in batch}
        payload = "\n\n".join(f"// {q}\n{s}" for q, s in skeletons.items())
        key = cache_key("discover", content_hash(payload), PROMPT_VERSION, None)
        cached = project.store.cache_get(key)
        if cached is not None:
            log(f"  cache hit for {len(batch)} classes")
            found.extend(cached.get("endpoints", []))
        else:
            log(f"  asking model about {len(batch)} classes (~{batch_tok} tokens)")
            try:
                resp = client.complete(DISCOVER_SYSTEM, "```java\n" + payload + "\n```", json_mode=True)
                data = extract_json(resp.text)
                eps = [e for e in data.get("endpoints", []) if isinstance(e, dict) and e.get("handler")]
                project.store.cache_put(key, "discover", ",".join(batch), content_hash(payload), client.name, client.model,
                                        PROMPT_VERSION, {"endpoints": eps}, resp.input_tokens, resp.output_tokens)
                project.store.log_call(client.name, client.model, "discover", batch, resp.input_tokens, resp.output_tokens,
                                       resp.cache_read_tokens, resp.cache_write_tokens, 0, "ok")
                found.extend(eps)
            except (LLMError, ValueError) as e:
                log(f"  ! {e}")
        batch, batch_tok = [], 0

    for q in candidates:
        tok = estimate_tokens(class_skeleton(index, q))
        if batch and batch_tok + tok > batch_tokens:
            flush()
        batch.append(q)
        batch_tok += tok
    flush()
    out = []
    for e in found:
        try:
            conf = float(e.get("confidence", 1.0))
        except (TypeError, ValueError):
            conf = 1.0
        if conf < min_confidence:
            continue
        handler = str(e["handler"])
        if "#" not in handler:
            continue
        tq = handler.split("#", 1)[0]
        if tq not in index.types:
            # try simple-name match
            simple = tq.rsplit(".", 1)[-1]
            cands = index.simple.get(simple, [])
            if len(cands) != 1:
                continue
            handler = cands[0] + "#" + handler.split("#", 1)[1]
        out.append({"handler": handler, "method": str(e.get("method", "POST")).upper(), "path": str(e.get("path", "")),
                    **({"body": e["body"]} if e.get("body") else {}),
                    **({"summary": e["summary"]} if e.get("summary") else {}), "confidence": conf})
    return out


def write_endpoints_file(path, endpoints: List[Dict[str, Any]], merge: bool = True) -> int:
    existing: List[Dict[str, Any]] = []
    if merge and path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        existing = data.get("endpoints", []) or []
    have = {e.get("handler") for e in existing}
    added = 0
    for e in endpoints:
        if e["handler"] in have:
            continue
        existing.append(e)
        added += 1
    path.write_text(yaml.safe_dump({"endpoints": existing}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return added
