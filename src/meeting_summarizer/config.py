"""Environment loading and OpenAI client construction.

The API key is read from the ``OPENAI_API_KEY`` environment variable.
A local ``.env`` file is loaded when present; real secrets stay out of git.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI

from meeting_summarizer.exceptions import ConfigurationError, MissingAPIKeyError

DEFAULT_CHAT_MODEL = "gpt-4o-mini"
DEFAULT_TRANSCRIPTION_MODEL = "whisper-1"
ALLOWED_TRANSCRIPTION_MODELS = frozenset({"whisper-1", "gpt-4o-transcribe"})

ENV_API_KEY = "OPENAI_API_KEY"
ENV_CHAT_MODEL = "OPENAI_MODEL"
ENV_TRANSCRIPTION_MODEL = "OPENAI_TRANSCRIPTION_MODEL"

# Whisper's public upload limit. gpt-4o-transcribe uses the same ceiling.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


def load_env() -> None:
    """Load a ``.env`` file from the working directory, if one exists.

    Existing environment variables win over values in the file.
    """
    load_dotenv(override=False)


def get_api_key() -> str:
    """Return the OpenAI API key or raise ``MissingAPIKeyError``."""
    load_env()
    api_key = os.getenv(ENV_API_KEY, "").strip()
    if not api_key:
        raise MissingAPIKeyError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return api_key


def build_openai_client(api_key: str | None = None) -> OpenAI:
    """Construct an OpenAI client from an explicit key or the environment."""
    return OpenAI(api_key=api_key or get_api_key(), timeout=120.0)


def resolve_chat_model(explicit: str | None = None) -> str:
    """Choose the chat model: flag, then ``OPENAI_MODEL``, then the default."""
    load_env()
    if explicit is not None and explicit.strip():
        candidate = explicit.strip()
    else:
        candidate = os.getenv(ENV_CHAT_MODEL, "").strip() or DEFAULT_CHAT_MODEL
    if not candidate:
        raise ConfigurationError("The chat model name is empty.")
    return candidate


def resolve_transcription_model(explicit: str | None = None) -> str:
    """Choose a supported transcription model.

    Accepted values are ``whisper-1`` and ``gpt-4o-transcribe``.
    An explicit value wins, then ``OPENAI_TRANSCRIPTION_MODEL``, then the default.
    """
    load_env()
    if explicit is not None and explicit.strip():
        candidate = explicit.strip()
    else:
        candidate = (
            os.getenv(ENV_TRANSCRIPTION_MODEL, "").strip() or DEFAULT_TRANSCRIPTION_MODEL
        )
    if candidate not in ALLOWED_TRANSCRIPTION_MODELS:
        allowed = ", ".join(sorted(ALLOWED_TRANSCRIPTION_MODELS))
        raise ConfigurationError(
            f"Unsupported transcription model '{candidate}'. Choose one of: {allowed}."
        )
    return candidate
