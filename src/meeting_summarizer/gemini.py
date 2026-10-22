"""Transcribe audio with Gemini's free tier.

Chat completions use Gemini's OpenAI-compatible endpoint. Audio does not, so
transcription calls ``generateContent`` with inline audio. The request stays
under Gemini's inline size limit because callers split larger files first.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from meeting_summarizer.config import get_api_key, resolve_transcription_model
from meeting_summarizer.exceptions import EmptyTranscriptError, ProviderRequestError
from meeting_summarizer.retry import call_with_backoff

_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
}


def transcribe_with_gemini(path: Path, *, model: str | None = None) -> str:
    """Return a verbatim transcript from Gemini's ``generateContent`` API."""
    selected = resolve_transcription_model(model, provider="gemini")
    api_key = get_api_key("gemini")
    mime = _MIME.get(path.suffix.lower(), "application/octet-stream")
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Transcribe this meeting audio verbatim. "
                            "Return only the transcript text, with speaker names when they are spoken."
                        )
                    },
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"temperature": 0},
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{selected}:generateContent"
    )
    body = json.dumps(payload).encode("utf-8")

    def attempt() -> dict:
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            headers = {"retry-after": retry_after} if retry_after else {}
            raise _HttpStatusError(exc.code, detail or exc.reason, headers) from exc
        except urllib.error.URLError as exc:
            raise ProviderRequestError(f"Gemini transcription failed: {exc.reason}") from exc

    try:
        result = call_with_backoff(attempt)
    except _HttpStatusError as exc:
        raise ProviderRequestError(f"Gemini transcription failed: {exc}") from exc

    text = _transcript_text(result).strip()
    if not text:
        raise EmptyTranscriptError(f"No speech was detected in {path.name}.")
    return text


def _transcript_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    return "\n".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


class _HttpStatusError(Exception):
    """HTTP error with the status code ``call_with_backoff`` already understands."""

    def __init__(self, status_code: int, message: str, headers: dict[str, str]) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, headers=headers)
