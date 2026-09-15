import os
import stat
from pathlib import Path

import pytest

from apidocgen.config import Config
from apidocgen.llm import LLMError, make_client
from setup_openai import save_key


def test_key_file_is_resolved_relative_to_config(tmp_path):
    secret = tmp_path / "secrets" / "key"
    save_key(secret, "example-not-a-real-key")
    cfg = Config({"llm": {"provider": "mock", "api_key_env": "APIDOCGEN_TEST_MISSING", "api_key_file": "secrets/key"}}, tmp_path / "apidocgen.yaml")
    assert cfg.llm_api_key() == "example-not-a-real-key"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    secret.chmod(0o644)
    save_key(secret, "replacement-test-key")
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert cfg.llm_api_key() == "replacement-test-key"


def test_missing_key_file_returns_none(tmp_path):
    cfg = Config({"llm": {"provider": "mock", "api_key_env": "APIDOCGEN_TEST_MISSING", "api_key_file": "missing"}}, tmp_path / "apidocgen.yaml")
    assert cfg.llm_api_key() is None


def test_environment_takes_precedence_over_file(tmp_path):
    name = "APIDOCGEN_TEST_KEY_PRECEDENCE"
    old = os.environ.get(name)
    try:
        os.environ[name] = "environment-test-key"
        save_key(tmp_path / "key", "file-test-key")
        cfg = Config({"llm": {"provider": "mock", "api_key_env": name, "api_key_file": "key"}}, tmp_path / "apidocgen.yaml")
        assert cfg.llm_api_key() == "environment-test-key"
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


def test_empty_key_does_not_overwrite_existing_secret(tmp_path):
    path = tmp_path / "key"
    save_key(path, "test-key")
    with pytest.raises(ValueError):
        save_key(path, "   ")
    assert path.read_text().strip() == "test-key"


def test_official_openai_fails_before_sending_without_key():
    with pytest.raises(LLMError):
        make_client({"provider": "openai", "model": "gpt-5.6-terra"}, None)
    assert make_client({"provider": "openai", "model": "local", "base_url": "http://localhost:1234/v1"}, None).api_key is None
