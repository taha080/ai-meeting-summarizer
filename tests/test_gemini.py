"""Tests for optional Gemini transcription. No network calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_summarizer.transcribe import transcribe_audio


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


def test_gemini_transcribes_inline_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "notes.mp3"
    audio.write_bytes(b"not-really-audio")
    seen: dict[str, object] = {}

    def urlopen(request: object, timeout: float = 0) -> _Response:
        headers = {key.lower(): value for key, value in request.header_items()}  # type: ignore[attr-defined]
        seen["key"] = headers.get("x-goog-api-key")
        seen["url"] = request.full_url  # type: ignore[attr-defined]
        seen["timeout"] = timeout
        body = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        mime = body["contents"][0]["parts"][1]["inline_data"]["mime_type"]
        seen["mime"] = mime
        return _Response(
            {"candidates": [{"content": {"parts": [{"text": "We agreed to ship."}]}}]}
        )

    monkeypatch.setattr("meeting_summarizer.config.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.setattr("meeting_summarizer.gemini.urllib.request.urlopen", urlopen)

    text = transcribe_audio(audio, provider="gemini")

    assert text == "We agreed to ship."
    assert seen["key"] == "gem-test"
    assert seen["mime"] == "audio/mpeg"
    assert "gemini-2.5-flash:generateContent" in str(seen["url"])
