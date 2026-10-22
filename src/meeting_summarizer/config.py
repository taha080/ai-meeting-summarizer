"""Environment loading and free-tier provider defaults.

Groq is the default and is free: create a key at https://console.groq.com/keys
with no credit card. OpenAI and Gemini are optional.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI

from meeting_summarizer.exceptions import ConfigurationError, MissingAPIKeyError

DEFAULT_PROVIDER = "groq"
PROVIDERS = ("groq", "openai", "gemini")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Groq free-tier defaults. gpt-oss-20b and gpt-oss-120b are each capped at
# 8,000 tokens per minute. Llama models are no longer on the free plan.
GROQ_CHAT_MODEL = "openai/gpt-oss-20b"
GROQ_FALLBACK_MODEL = "openai/gpt-oss-120b"
GROQ_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"

OPENAI_CHAT_MODEL = "gpt-4o-mini"
OPENAI_TRANSCRIPTION_MODEL = "whisper-1"

GEMINI_MODEL = "gemini-2.5-flash"

# Names kept for callers that want "the" default. Those follow the Groq free tier.
DEFAULT_CHAT_MODEL = GROQ_CHAT_MODEL
DEFAULT_TRANSCRIPTION_MODEL = GROQ_TRANSCRIPTION_MODEL

TRANSCRIPTION_MODELS = {
    "groq": frozenset({"whisper-large-v3-turbo", "whisper-large-v3"}),
    "openai": frozenset({"whisper-1", "gpt-4o-transcribe"}),
}

# Groq free tier for openai/gpt-oss-20b and openai/gpt-oss-120b.
# One request must fit in this budget.
FREE_TIER_TPM = 8000
# Over-estimate tokens (about 3 characters each) so a chunk cannot silently exceed 8k.
CHARS_PER_TOKEN = 3
COMPLETION_RESERVE_TOKENS = 1000
DEFAULT_CHUNK_TOKENS = 2000
DEFAULT_CHUNK_CHARS = DEFAULT_CHUNK_TOKENS * CHARS_PER_TOKEN
DEFAULT_CHUNK_OVERLAP = 400

# Groq's free-tier upload cap. Larger recordings are split before upload.
MAX_AUDIO_BYTES = 25 * 1024 * 1024
# Gemini inline audio must stay under the ~20 MB generateContent request limit.
GEMINI_INLINE_BYTES = 15 * 1024 * 1024
# Safety cap for the web upload. Files between this and the provider limit are split.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_KEY_HELP = {
    "groq": (
        "GROQ_API_KEY is not set. Create a free key at https://console.groq.com/keys "
        "(no credit card) and add it to .env."
    ),
    "openai": (
        "OPENAI_API_KEY is not set. OpenAI is optional and paid. "
        "Set PROVIDER=groq to use the free Groq tier instead."
    ),
    "gemini": (
        "GEMINI_API_KEY is not set. Create a free key in Google AI Studio "
        "(https://aistudio.google.com/apikey) and add it to .env."
    ),
}


def load_env() -> None:
    """Load a ``.env`` file from the working directory, if one exists.

    Existing environment variables win over values in the file.
    """
    load_dotenv(override=False)


def resolve_provider(explicit: str | None = None) -> str:
    """Choose ``groq`` (the default), ``openai``, or ``gemini``."""
    load_env()
    if explicit is not None and explicit.strip():
        candidate = explicit.strip().lower()
    else:
        candidate = os.getenv("PROVIDER", "").strip().lower() or DEFAULT_PROVIDER
    if candidate not in PROVIDERS:
        allowed = ", ".join(PROVIDERS)
        raise ConfigurationError(f"Unsupported provider '{candidate}'. Choose one of: {allowed}.")
    return candidate


def get_api_key(provider: str | None = None) -> str:
    """Return the API key for ``provider`` or raise ``MissingAPIKeyError``."""
    selected = resolve_provider(provider)
    api_key = os.getenv(_KEY_ENV[selected], "").strip()
    if not api_key:
        raise MissingAPIKeyError(_KEY_HELP[selected])
    return api_key


def build_client(provider: str | None = None, api_key: str | None = None) -> OpenAI:
    """Build an OpenAI SDK client pointed at the selected provider.

    Groq and Gemini are used through OpenAI-compatible base URLs, so the
    ``openai`` package is only an HTTP client. The default base URL is Groq's
    free endpoint.
    """
    selected = resolve_provider(provider)
    key = api_key or get_api_key(selected)
    if selected == "groq":
        base_url = os.getenv("GROQ_BASE_URL", "").strip() or GROQ_BASE_URL
        return OpenAI(api_key=key, base_url=base_url, timeout=120.0)
    if selected == "gemini":
        return OpenAI(api_key=key, base_url=GEMINI_OPENAI_BASE_URL, timeout=120.0)
    return OpenAI(api_key=key, timeout=120.0)


def resolve_chat_model(explicit: str | None = None, *, provider: str | None = None) -> str:
    """Choose the chat model: flag, then the provider's env var, then its default."""
    selected = resolve_provider(provider)
    load_env()
    if explicit is not None and explicit.strip():
        candidate = explicit.strip()
    else:
        env_name = {
            "groq": "GROQ_MODEL",
            "openai": "OPENAI_MODEL",
            "gemini": "GEMINI_MODEL",
        }[selected]
        fallback = {
            "groq": GROQ_CHAT_MODEL,
            "openai": OPENAI_CHAT_MODEL,
            "gemini": GEMINI_MODEL,
        }[selected]
        candidate = os.getenv(env_name, "").strip() or fallback
    if not candidate:
        raise ConfigurationError("The chat model name is empty.")
    return candidate


def resolve_fallback_model(provider: str | None = None) -> str | None:
    """Return Groq's fallback chat model, or ``None`` for other providers.

    The default fallback is ``openai/gpt-oss-120b``. Llama models are no longer
    on Groq's free plan. Set ``GROQ_FALLBACK_MODEL`` empty to disable fallback.
    """
    selected = resolve_provider(provider)
    if selected != "groq":
        return None
    load_env()
    if "GROQ_FALLBACK_MODEL" in os.environ:
        configured = os.getenv("GROQ_FALLBACK_MODEL", "").strip()
        return configured or None
    return GROQ_FALLBACK_MODEL


def resolve_transcription_model(
    explicit: str | None = None,
    *,
    provider: str | None = None,
) -> str:
    """Choose a transcription model allowed for the selected provider."""
    selected = resolve_provider(provider)
    load_env()
    if explicit is not None and explicit.strip():
        candidate = explicit.strip()
    else:
        env_name = {
            "groq": "GROQ_TRANSCRIPTION_MODEL",
            "openai": "OPENAI_TRANSCRIPTION_MODEL",
            "gemini": "GEMINI_MODEL",
        }[selected]
        fallback = {
            "groq": GROQ_TRANSCRIPTION_MODEL,
            "openai": OPENAI_TRANSCRIPTION_MODEL,
            "gemini": GEMINI_MODEL,
        }[selected]
        candidate = os.getenv(env_name, "").strip() or fallback
    if not candidate:
        raise ConfigurationError("The transcription model name is empty.")
    allowed = TRANSCRIPTION_MODELS.get(selected)
    if allowed is not None and candidate not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigurationError(
            f"Unsupported transcription model '{candidate}' for {selected}. Choose one of: {choices}."
        )
    return candidate
