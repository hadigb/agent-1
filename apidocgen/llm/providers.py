"""Concrete LLM providers: Anthropic (with prompt caching), OpenAI-compatible,
Ollama, and a deterministic mock used for tests and dry runs."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from .base import LLMClient, LLMError, LLMResponse, extract_json


class AnthropicClient(LLMClient):
    name = "anthropic"
    default_url = "https://api.anthropic.com"

    def complete(self, system: str, user: str, json_mode: bool = True) -> LLMResponse:
        if not self.api_key:
            raise LLMError("Anthropic API key missing (set ANTHROPIC_API_KEY or llm.api_key)")
        url = (self.base_url or self.default_url).rstrip("/") + "/v1/messages"
        system_block: Dict[str, Any] = {"type": "text", "text": system}
        if self.options.get("prompt_cache", True):
            system_block["cache_control"] = {"type": "ephemeral"}
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "system": [system_block],
            "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        prefill = bool(json_mode and self.options.get("prefill", True))
        if prefill:
            # prefill the assistant turn so the model starts with the object directly
            payload["messages"].append({"role": "assistant", "content": "{"})
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        try:
            data = self._post_json(url, payload, headers)
        except LLMError as e:
            if prefill and e.status == 400 and ("prefill" in str(e).lower() or "assistant" in str(e).lower()):
                # model / mode without prefill support: retry plainly and remember it for the next calls
                self.options["prefill"] = False
                payload["messages"] = payload["messages"][:1]
                data = self._post_json(url, payload, headers)
                prefill = False
            else:
                raise
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if prefill:
            text = "{" + text
        usage = data.get("usage", {}) or {}
        stop = data.get("stop_reason") or ""
        return LLMResponse(text=text, input_tokens=int(usage.get("input_tokens", 0)),
                           output_tokens=int(usage.get("output_tokens", 0)),
                           cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
                           cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
                           stop_reason="length" if stop == "max_tokens" else ("stop" if stop in ("end_turn", "stop_sequence") else stop),
                           raw=data)


class OpenAIClient(LLMClient):
    name = "openai"
    default_url = "https://api.openai.com/v1"

    def complete(self, system: str, user: str, json_mode: bool = True) -> LLMResponse:
        url = (self.base_url or self.default_url).rstrip("/") + "/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_output_tokens:
            payload[self.options.get("max_tokens_param", "max_completion_tokens")] = self.max_output_tokens
        if json_mode and self.options.get("json_mode", True):
            payload["response_format"] = {"type": "json_object"}
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            data = self._post_json(url, payload, headers)
        except LLMError as e:
            # some servers reject max_completion_tokens or response_format - retry once in a compatible mode
            if e.status == 400 and ("max_completion_tokens" in str(e) or "response_format" in str(e) or "unsupported" in str(e).lower()):
                payload.pop("response_format", None)
                payload.pop("max_completion_tokens", None)
                payload["max_tokens"] = self.max_output_tokens
                data = self._post_json(url, payload, headers)
            else:
                raise
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"no choices in response: {json.dumps(data)[:500]}")
        msg = choices[0].get("message", {})
        text = msg.get("content") or ""
        if isinstance(text, list):  # some servers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        usage = data.get("usage", {}) or {}
        details = usage.get("prompt_tokens_details") or {}
        finish = choices[0].get("finish_reason") or ""
        return LLMResponse(text=text, input_tokens=int(usage.get("prompt_tokens", 0)),
                           output_tokens=int(usage.get("completion_tokens", 0)),
                           cache_read_tokens=int(details.get("cached_tokens", 0) or 0),
                           stop_reason="length" if finish == "length" else ("stop" if finish == "stop" else finish), raw=data)


class OllamaClient(LLMClient):
    name = "ollama"
    default_url = "http://localhost:11434"

    def complete(self, system: str, user: str, json_mode: bool = True) -> LLMResponse:
        url = (self.base_url or self.default_url).rstrip("/") + "/api/chat"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {},
        }
        if self.temperature is not None:
            payload["options"]["temperature"] = self.temperature
        if self.options.get("num_ctx"):
            payload["options"]["num_ctx"] = int(self.options["num_ctx"])
        if self.max_output_tokens:
            payload["options"]["num_predict"] = self.max_output_tokens
        if json_mode:
            payload["format"] = "json"
        data = self._post_json(url, payload, {})
        text = (data.get("message") or {}).get("content", "")
        done = data.get("done_reason") or ""
        return LLMResponse(text=text, input_tokens=int(data.get("prompt_eval_count", 0) or 0),
                           output_tokens=int(data.get("eval_count", 0) or 0),
                           stop_reason="length" if done == "length" else ("stop" if done == "stop" else done), raw=data)


class MockClient(LLMClient):
    """Deterministic stand-in: produces well-formed Persian placeholder output from the unit spec in the prompt.

    Used by the test-suite and by ``--provider mock`` to exercise the whole pipeline without network access.
    """
    name = "mock"

    def __init__(self, model: str = "mock-1", **kwargs: Any) -> None:
        super().__init__(model=model, **kwargs)
        self.calls: List[Dict[str, str]] = []

    def complete(self, system: str, user: str, json_mode: bool = True) -> LLMResponse:
        self.calls.append({"system": system, "user": user})
        if "\"kind\": \"usecase\"" in user or '"kind": "usecase"' in user or "Use Case Specifications" in system:
            m = re.search(r"### Units.*?\n(\{.*?\})\n\n### Source", user, re.S)
            results = {}
            units = []
            if m:
                units = json.loads(m.group(1)).get("units", [])
            for u in units:
                uid = u["id"]
                results[uid] = {
                    "use_case_id": f"UC-{uid.replace(' ', '-')}",
                    "name": f"مورد کاربرد {u.get('name', uid)}",
                    "actors": ["کاربر", "سامانه"],
                    "description": f"شرح آزمایشی مورد کاربرد {uid}",
                    "trigger": uid,
                    "preconditions": ["کاربر احراز هویت شده است."],
                    "postconditions": ["نتیجه در سامانه ثبت شده است."],
                    "main_flow": [{"number": 1, "actor": "کاربر", "action": "درخواست را ارسال می‌کند.", "is_new": False},
                                  {"number": 2, "actor": "سامانه", "action": "درخواست را اعتبارسنجی و اجرا می‌کند.", "is_new": bool("NEW REQUIREMENT" in user)}],
                    "alternative_flows": [],
                    "exception_flows": [],
                    "business_rules": ["قواعد موجود در پیاده‌سازی رعایت می‌شود."],
                    "special_requirements": ["نیازمندی جدید اعمال شود."] if "NEW REQUIREMENT" in user else [],
                    "notes": [],
                    "new_requirement_impacts": ["گام ۲ جریان اصلی"] if "NEW REQUIREMENT" in user else [],
                }
            text = json.dumps({"results": results}, ensure_ascii=False)
            return LLMResponse(text=text, input_tokens=len(user) // 4, output_tokens=len(text) // 4)
        m = re.search(r"### Units to document.*?\n(\{.*?\})\n\n### Source code", user, re.S)
        results: Dict[str, Any] = {}
        if m:
            spec = json.loads(m.group(1))
            for u in spec.get("units", []):
                uid = u["id"]
                if u["kind"] == "type":
                    fields = {f: {"description": f"توضیح {u['name']}.{f}", "example": self._example(f)} for f in u.get("fields", [])}
                    enum_values = {c: f"برچسب {c}" for c in u.get("enum_values", [])}
                    results[uid] = {"description": f"شرح نوع {u['name']}", "fields": fields, "enum_values": enum_values}
                else:
                    results[uid] = {
                        "title": f"سرویس {u['name'].split(' ', 1)[-1].strip('/').rsplit('/', 1)[-1]}",
                        "summary": f"خلاصه سرویس {u['name']}",
                        "description": f"شرح سرویس {u['name']} (تولید شده توسط مدل آزمایشی).",
                        "params": {p: f"توضیح پارامتر {p}" for p in u.get("params", [])},
                        "notes": ["نکته نمونه اول", "نکته نمونه دوم"],
                        "error_codes": [{"code": "1038", "title": "اطلاعات ورودی اشتباه است", "note": ""}],
                        "response_params": {p: f"توضیح خروجی {p}" for p in u.get("response_params", [])},
                        "response_fields_extra": [],
                        "business_rules": ["شروط موجود در پیاده‌سازی سرویس رعایت می‌شود."],
                    }
        text = json.dumps({"results": results}, ensure_ascii=False)
        approx_in = len(system) // 4 + len(user) // 4
        return LLMResponse(text=text, input_tokens=approx_in, output_tokens=len(text) // 4, raw=None)

    @staticmethod
    def _example(name: str) -> Any:
        n = name.lower()
        if n.startswith("is") or n.startswith("has"):
            return True
        if "amount" in n:
            return 100000
        if "date" in n:
            return "1403/09/23 - 11:13:35"
        return f"{name}-نمونه"


PROVIDERS = {"anthropic": AnthropicClient, "openai": OpenAIClient, "ollama": OllamaClient, "mock": MockClient}


def make_client(cfg_llm: Dict[str, Any], api_key: Optional[str]) -> LLMClient:
    provider = (cfg_llm.get("provider") or "anthropic").lower()
    if provider not in PROVIDERS:
        raise LLMError(f"unknown llm.provider {provider!r}; choose one of {sorted(PROVIDERS)}")
    if provider == "openai" and not api_key:
        from urllib.parse import urlparse
        url = cfg_llm.get("base_url") or OpenAIClient.default_url
        if urlparse(url).hostname == "api.openai.com":
            raise LLMError("OpenAI API key missing. Run setup_openai.py locally or set OPENAI_API_KEY.")
    cls = PROVIDERS[provider]
    return cls(model=cfg_llm.get("model") or "", api_key=api_key, base_url=cfg_llm.get("base_url"),
               temperature=cfg_llm.get("temperature", 0.2), max_output_tokens=int(cfg_llm.get("max_output_tokens", 4000)),
               timeout=int(cfg_llm.get("timeout_seconds", 180)), retries=int(cfg_llm.get("retries", 2)),
               extra_headers=cfg_llm.get("extra_headers") or {}, prompt_cache=cfg_llm.get("prompt_cache", True),
               json_mode=cfg_llm.get("json_mode", True), num_ctx=cfg_llm.get("num_ctx"),
               max_tokens_param=cfg_llm.get("max_tokens_param", "max_completion_tokens"))
