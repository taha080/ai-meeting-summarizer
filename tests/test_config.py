"""Tests for provider selection and free-tier defaults."""

from __future__ import annotations

import pytest

from meeting_summarizer.config import (
    GROQ_BASE_URL,
    GROQ_CHAT_MODEL,
    GROQ_FALLBACK_MODEL,
    GROQ_TRANSCRIPTION_MODEL,
    build_client,
    get_api_key,
    resolve_chat_model,
    resolve_fallback_model,
    resolve_provider,
    resolve_transcription_model,
)
from meeting_summarizer.exceptions import ConfigurationError, MissingAPIKeyError


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meeting_summarizer.config.load_dotenv", lambda *args, **kwargs: False)
    for name in (
        "PROVIDER",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "GROQ_FALLBACK_MODEL",
        "GROQ_TRANSCRIPTION_MODEL",
        "GROQ_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_TRANSCRIPTION_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_missing_groq_key_is_the_default_error(isolated_env: None) -> None:
    with pytest.raises(MissingAPIKeyError, match="GROQ_API_KEY"):
        get_api_key()
    with pytest.raises(MissingAPIKeyError, match="console.groq.com/keys"):
        get_api_key("groq")


def test_groq_client_uses_the_free_endpoint(
    isolated_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    class DummyClient:
        def __init__(self, *, api_key: str, timeout: float, base_url: str | None = None) -> None:
            self.api_key = api_key
            self.timeout = timeout
            self.base_url = base_url

    monkeypatch.setattr("meeting_summarizer.config.OpenAI", DummyClient)
    client = build_client()
    assert isinstance(client, DummyClient)
    assert client.api_key == "gsk-test"
    assert client.base_url == GROQ_BASE_URL


def test_optional_providers(isolated_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyClient:
        def __init__(self, *, api_key: str, timeout: float, base_url: str | None = None) -> None:
            self.api_key = api_key
            self.base_url = base_url

    monkeypatch.setattr("meeting_summarizer.config.OpenAI", DummyClient)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-paid")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-free")

    openai_client = build_client("openai")
    assert openai_client.api_key == "sk-paid"
    assert openai_client.base_url is None

    gemini_client = build_client("gemini")
    assert gemini_client.api_key == "gem-free"
    assert gemini_client.base_url is not None
    assert "generativelanguage.googleapis.com" in gemini_client.base_url

    with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY"):
        monkeypatch.delenv("OPENAI_API_KEY")
        get_api_key("openai")


def test_free_tier_model_defaults(isolated_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_provider(None) == "groq"
    assert resolve_chat_model(None) == GROQ_CHAT_MODEL
    assert resolve_fallback_model() == GROQ_FALLBACK_MODEL
    assert resolve_transcription_model(None) == GROQ_TRANSCRIPTION_MODEL

    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_FALLBACK_MODEL", "")
    assert resolve_chat_model(None) == "openai/gpt-oss-20b"
    assert resolve_chat_model("  custom-model  ") == "custom-model"
    assert resolve_fallback_model() is None
    assert resolve_transcription_model("whisper-large-v3") == "whisper-large-v3"

    with pytest.raises(ConfigurationError, match="whisper-large-v3-turbo"):
        resolve_transcription_model("whisper-1")
    with pytest.raises(ConfigurationError, match="groq"):
        resolve_provider("azure")


def test_openai_transcription_models_when_selected(isolated_env: None) -> None:
    assert resolve_transcription_model("whisper-1", provider="openai") == "whisper-1"
    assert resolve_transcription_model(None, provider="gemini") == "gemini-2.5-flash"
    assert resolve_chat_model(None, provider="openai") == "gpt-4o-mini"
