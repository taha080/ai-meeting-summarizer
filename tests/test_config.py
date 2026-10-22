"""Tests for environment loading and model selection."""

from __future__ import annotations

import pytest

from meeting_summarizer.config import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_TRANSCRIPTION_MODEL,
    build_openai_client,
    get_api_key,
    resolve_chat_model,
    resolve_transcription_model,
)
from meeting_summarizer.exceptions import ConfigurationError, MissingAPIKeyError


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meeting_summarizer.config.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_TRANSCRIPTION_MODEL", raising=False)


def test_missing_api_key(isolated_env: None) -> None:
    with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY"):
        get_api_key()


def test_api_key_and_client_use_the_environment(
    isolated_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class DummyClient:
        def __init__(self, *, api_key: str, timeout: float) -> None:
            self.api_key = api_key
            self.timeout = timeout

    monkeypatch.setattr("meeting_summarizer.config.OpenAI", DummyClient)
    assert get_api_key() == "sk-test"
    client = build_openai_client()
    assert isinstance(client, DummyClient)
    assert client.api_key == "sk-test"
    explicit = build_openai_client("sk-explicit")
    assert explicit.api_key == "sk-explicit"


def test_model_resolution_prefers_explicit_values(
    isolated_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    monkeypatch.setenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")

    assert resolve_chat_model(None) == "gpt-4o"
    assert resolve_chat_model("  gpt-4.1-mini  ") == "gpt-4.1-mini"
    assert resolve_transcription_model(None) == "gpt-4o-transcribe"
    assert resolve_transcription_model("whisper-1") == "whisper-1"


def test_model_defaults_and_invalid_transcription_model(isolated_env: None) -> None:
    assert resolve_chat_model(None) == DEFAULT_CHAT_MODEL
    assert resolve_transcription_model(None) == DEFAULT_TRANSCRIPTION_MODEL
    with pytest.raises(ConfigurationError, match="whisper-1"):
        resolve_transcription_model("not-a-model")
