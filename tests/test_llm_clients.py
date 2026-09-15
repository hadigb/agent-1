"""The provider clients are exercised against a stubbed urlopen: we check the
exact request payloads (prompt caching block, json mode, auth headers) and the
usage accounting."""
import io
import json
import urllib.request

import pytest

from apidocgen.llm import AnthropicClient, LLMError, OllamaClient, OpenAIClient, make_client


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub(monkeypatch, response: dict, capture: list, status_error: int | None = None):
    def fake_urlopen(req, timeout=0):
        capture.append({"url": req.full_url, "headers": dict(req.header_items()), "body": json.loads(req.data.decode("utf-8"))})
        if status_error:
            raise urllib.error.HTTPError(req.full_url, status_error, "err", {}, io.BytesIO(b'{"error":"boom"}'))
        return _Resp(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_anthropic_payload_and_usage(monkeypatch):
    cap = []
    _stub(monkeypatch, {"content": [{"type": "text", "text": '"results": {"x": {"description": "d"}}}'}],
                        "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0}}, cap)
    c = AnthropicClient(model="claude-sonnet-5", api_key="k", prompt_cache=True, temperature=0.1)
    r = c.complete("SYS", "USER", json_mode=True)
    body = cap[0]["body"]
    assert cap[0]["headers"]["X-api-key"] == "k" and cap[0]["headers"]["Anthropic-version"] == "2023-06-01"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"} and body["system"][0]["text"] == "SYS"
    assert body["messages"][0]["role"] == "user" and body["messages"][1] == {"role": "assistant", "content": "{"}
    assert body["temperature"] == 0.1 and body["model"] == "claude-sonnet-5"
    assert json.loads(r.text) == {"results": {"x": {"description": "d"}}}
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens) == (120, 30, 100)


def test_anthropic_requires_key():
    with pytest.raises(LLMError):
        AnthropicClient(model="m", api_key=None).complete("s", "u")


def test_openai_payload(monkeypatch):
    cap = []
    _stub(monkeypatch, {"choices": [{"message": {"content": '{"results": {}}'}}],
                        "usage": {"prompt_tokens": 50, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 40}}}, cap)
    c = OpenAIClient(model="gpt-x", api_key="sk", base_url="http://localhost:1234/v1", temperature=None, json_mode=True)
    r = c.complete("SYS", "USER")
    body = cap[0]["body"]
    assert cap[0]["url"] == "http://localhost:1234/v1/chat/completions"
    assert cap[0]["headers"]["Authorization"] == "Bearer sk"
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["response_format"] == {"type": "json_object"} and "temperature" not in body
    assert r.cache_read_tokens == 40 and r.input_tokens == 50


def test_ollama_payload(monkeypatch):
    cap = []
    _stub(monkeypatch, {"message": {"content": "{}"}, "prompt_eval_count": 10, "eval_count": 2}, cap)
    r = OllamaClient(model="qwen", base_url="http://localhost:11434").complete("S", "U")
    assert cap[0]["url"] == "http://localhost:11434/api/chat" and cap[0]["body"]["format"] == "json"
    assert r.input_tokens == 10 and r.output_tokens == 2


def test_http_error_is_reported(monkeypatch):
    cap = []
    _stub(monkeypatch, {}, cap, status_error=401)
    c = OpenAIClient(model="m", api_key="k", retries=0)
    with pytest.raises(LLMError) as ei:
        c.complete("s", "u")
    assert ei.value.status == 401 and not ei.value.retryable


def test_make_client_factory():
    c = make_client({"provider": "mock", "model": "mock-1"}, None)
    assert c.name == "mock"
    with pytest.raises(LLMError):
        make_client({"provider": "nope"}, None)
