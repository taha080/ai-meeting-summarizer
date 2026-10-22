"""Load a plain-text transcript or transcribe meeting audio with OpenAI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError

from meeting_summarizer.config import (
    MAX_AUDIO_BYTES,
    build_openai_client,
    resolve_transcription_model,
)
from meeting_summarizer.exceptions import (
    AudioTooLargeError,
    EmptyTranscriptError,
    SummarizerError,
    UnsupportedMediaError,
)

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
) -> str:
    """Transcribe an audio file with ``whisper-1`` or ``gpt-4o-transcribe``.

    Args:
        path: Existing ``.mp3``, ``.wav``, or ``.m4a`` file.
        client: OpenAI client. Built from the environment when omitted.
        model: Transcription model. Defaults to the configured model.

    Raises:
        UnsupportedMediaError: The suffix is not a supported audio type.
        AudioTooLargeError: The file is larger than 25 MB.
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

    size = path.stat().st_size
    if size == 0:
        raise EmptyTranscriptError(f"{path.name} is empty.")
    if size > MAX_AUDIO_BYTES:
        raise AudioTooLargeError(
            f"{path.name} is larger than the 25 MB transcription limit."
        )

    transcription_model = resolve_transcription_model(model)
    openai_client = client or build_openai_client()
    try:
        with path.open("rb") as audio_file:
            result = openai_client.audio.transcriptions.create(
                model=transcription_model,
                file=(path.name, audio_file, _CONTENT_TYPES[suffix]),
            )
    except OpenAIError as exc:
        raise SummarizerError(f"Transcription failed: {exc}") from exc

    text = _transcription_text(result)
    if not text:
        raise EmptyTranscriptError(f"No speech was detected in {path.name}.")
    return text


def load_transcript(
    path: Path,
    *,
    client: OpenAI | None = None,
    transcription_model: str | None = None,
) -> str:
    """Return transcript text from a ``.txt`` file or by transcribing audio.

    Text files are read locally and never sent to the transcription API.
    Audio files are uploaded to OpenAI for transcription.
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
        )
    raise UnsupportedMediaError(
        f"Unsupported file type '{suffix or path.name}'. Use .mp3, .wav, .m4a, or .txt."
    )
