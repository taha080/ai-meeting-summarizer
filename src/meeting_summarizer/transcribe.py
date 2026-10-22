"""Load a plain-text transcript or transcribe meeting audio.

The default provider is Groq's free API (``whisper-large-v3-turbo``). Files
larger than the provider's upload cap are split before they are sent.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError

from meeting_summarizer.audio_split import prepare_audio_segments
from meeting_summarizer.config import (
    GEMINI_INLINE_BYTES,
    MAX_AUDIO_BYTES,
    build_client,
    resolve_provider,
    resolve_transcription_model,
)
from meeting_summarizer.exceptions import (
    EmptyTranscriptError,
    ProviderRequestError,
    SummarizerError,
    UnsupportedMediaError,
)
from meeting_summarizer.gemini import transcribe_with_gemini
from meeting_summarizer.retry import call_with_backoff, is_rate_limit

AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a"})
TEXT_EXTENSIONS = frozenset({".txt"})
SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | TEXT_EXTENSIONS

_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
}


def is_audio_path(path: Path) -> bool:
    """Return whether ``path`` points at a supported audio file."""
    return path.suffix.lower() in AUDIO_EXTENSIONS


def is_supported_path(path: Path) -> bool:
    """Return whether ``path`` is a supported audio or transcript file."""
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _transcription_text(result: Any) -> str:
    """Read text from a string response or an SDK transcription object."""
    if isinstance(result, str):
        return result.strip()
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text.strip()
    raise SummarizerError("The transcription response did not include text.")


def transcribe_audio(
    path: Path,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
    provider: str | None = None,
) -> str:
    """Transcribe an audio file with the selected provider.

    Groq's free tier uses ``whisper-large-v3-turbo`` and accepts 25 MB per
    request. Larger files are split first. Gemini transcribes inline audio
    on its free tier. A 429 is retried with exponential backoff.

    Args:
        path: Existing ``.mp3``, ``.wav``, or ``.m4a`` file.
        client: OpenAI-compatible client. Built from the environment when omitted.
        model: Transcription model. Defaults to the provider's free model.
        provider: ``groq``, ``openai``, or ``gemini``. Defaults to ``groq``.

    Raises:
        UnsupportedMediaError: The suffix is not a supported audio type.
        AudioTooLargeError: The file is over the cap and could not be split.
        EmptyTranscriptError: The file or the transcript is empty.
        SummarizerError: The transcription request failed.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise UnsupportedMediaError(
            f"Cannot transcribe '{suffix or path.name}'. Use .mp3, .wav, or .m4a."
        )
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")
    if path.stat().st_size == 0:
        raise EmptyTranscriptError(f"{path.name} is empty.")

    selected = resolve_provider(provider)
    transcription_model = resolve_transcription_model(model, provider=selected)
    limit = GEMINI_INLINE_BYTES if selected == "gemini" else MAX_AUDIO_BYTES
    segments, temp_dir = prepare_audio_segments(path, max_bytes=limit)
    try:
        parts: list[str] = []
        for segment in segments:
            if selected == "gemini":
                text = transcribe_with_gemini(segment, model=transcription_model)
            else:
                text = _transcribe_segment(
                    segment,
                    client=client,
                    provider=selected,
                    model=transcription_model,
                )
            if text.strip():
                parts.append(text.strip())
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if not parts:
        raise EmptyTranscriptError(f"No speech was detected in {path.name}.")
    return "\n".join(parts)


def _transcribe_segment(
    path: Path,
    *,
    client: OpenAI | None,
    provider: str,
    model: str,
) -> str:
    openai_client = client or build_client(provider)
    suffix = path.suffix.lower()

    def attempt() -> Any:
        with path.open("rb") as audio_file:
            return openai_client.audio.transcriptions.create(
                model=model,
                file=(path.name, audio_file, _CONTENT_TYPES.get(suffix, "application/octet-stream")),
            )

    try:
        result = call_with_backoff(attempt)
    except OpenAIError as exc:
        raise ProviderRequestError(f"Transcription failed: {exc}") from exc
    except Exception as exc:
        if is_rate_limit(exc):
            raise ProviderRequestError(f"Transcription failed: {exc}") from exc
        raise
    return _transcription_text(result)


def load_transcript(
    path: Path,
    *,
    client: OpenAI | None = None,
    transcription_model: str | None = None,
    provider: str | None = None,
) -> str:
    """Return transcript text from a ``.txt`` file or by transcribing audio.

    Text files are read locally and are never sent to a transcription API.
    """
    path = Path(path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")

    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        text = path.read_text(encoding="utf-8-sig").strip()
        if not text:
            raise EmptyTranscriptError(f"{path.name} is empty.")
        return text
    if suffix in AUDIO_EXTENSIONS:
        return transcribe_audio(
            path,
            client=client,
            model=transcription_model,
            provider=provider,
        )
    raise UnsupportedMediaError(
        f"Unsupported file type '{suffix or path.name}'. Use .mp3, .wav, .m4a, or .txt."
    )
