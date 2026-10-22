"""Tests for the FastAPI upload UI."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_summarizer.models import MeetingSummary
from meeting_summarizer.web.app import app

client = TestClient(app)


def test_index_and_health() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "Meeting Summarizer" in page.text
    assert 'id="file"' in page.text
    assert "whisper-1" in page.text
    assert "gpt-4o-transcribe" in page.text
    assert client.get("/health").json() == {"status": "ok"}


def test_upload_text_returns_summary(
    monkeypatch: pytest.MonkeyPatch, sample_summary: MeetingSummary
) -> None:
    seen: dict[str, object] = {}

    def fake_summarize(path: Path, **kwargs: object) -> MeetingSummary:
        seen["text"] = Path(path).read_text(encoding="utf-8")
        seen["suffix"] = Path(path).suffix
        seen["model"] = kwargs["model"]
        seen["transcription_model"] = kwargs["transcription_model"]
        return sample_summary

    monkeypatch.setattr("meeting_summarizer.web.app.summarize_file", fake_summarize)
    response = client.post(
        "/api/summarize",
        files={"file": ("notes.txt", b"We agreed to ship.", "text/plain")},
        data={"model": "gpt-4o", "transcription_model": "whisper-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == sample_summary.summary
    assert body["action_items"][0]["owner"] == "Luis Ortega"
    assert body["action_items"][0]["due_date"] == "Thursday, October 22"
    assert body["key_decisions"] == ["Android tablet support is deferred to next quarter."]
    assert seen == {
        "text": "We agreed to ship.",
        "suffix": ".txt",
        "model": "gpt-4o",
        "transcription_model": "whisper-1",
    }


def test_upload_rejects_bad_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meeting_summarizer.web.app.MAX_AUDIO_BYTES", 8)

    unsupported = client.post(
        "/api/summarize",
        files={"file": ("slides.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert unsupported.status_code == 400
    assert "Unsupported file type" in unsupported.json()["detail"]

    empty = client.post(
        "/api/summarize",
        files={"file": ("empty.txt", b"   ", "text/plain")},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"] == "empty.txt is empty."

    huge = client.post(
        "/api/summarize",
        files={"file": ("meeting.mp3", b"0123456789", "audio/mpeg")},
    )
    assert huge.status_code == 413
    assert "25 MB" in huge.json()["detail"]


def test_missing_api_key_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meeting_summarizer.config.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post(
        "/api/summarize",
        files={"file": ("notes.txt", b"Hello team.", "text/plain")},
    )
    assert response.status_code == 500
    assert "OPENAI_API_KEY" in response.json()["detail"]
