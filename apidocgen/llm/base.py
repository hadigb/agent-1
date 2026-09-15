"""LLM client abstraction (stdlib HTTP only, no SDK dependencies)."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class LLMError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str = ""          # normalised: "stop" | "length" | other provider value
    raw: Any = field(default=None, repr=False)

    @property
    def truncated(self) -> bool:
        return self.stop_reason in ("length", "max_tokens")


class LLMClient:
    name = "base"

    def __init__(self, model: str, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 temperature: Optional[float] = 0.2, max_output_tokens: int = 4000, timeout: int = 180,
                 retries: int = 2, extra_headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.retries = retries
        self.extra_headers = extra_headers or {}
        self.options = kwargs

    # -- to implement -----------------------------------------------------
    def complete(self, system: str, user: str, json_mode: bool = True) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    # -- helpers ----------------------------------------------------------
    def _post_json(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Accept": "application/json", **self.extra_headers, **headers}
        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                    return json.loads(body)
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                retryable = e.code in (408, 409, 429, 500, 502, 503, 504, 529)
                last_err = LLMError(f"HTTP {e.code} from {url}: {body[:800]}", status=e.code, retryable=retryable)
                if not retryable or attempt >= self.retries:
                    raise last_err
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = LLMError(f"connection error calling {url}: {e}", retryable=True)
                if attempt >= self.retries:
                    raise last_err
            time.sleep(2 * (attempt + 1))
        raise last_err or LLMError("unknown error")


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> Dict[str, Any]:
    """Pull the first JSON object out of a model response (tolerates fences and chatter)."""
    if not text:
        raise ValueError("empty response")
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in response")
    candidate = t[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # remove trailing commas and retry
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
        return json.loads(fixed)
