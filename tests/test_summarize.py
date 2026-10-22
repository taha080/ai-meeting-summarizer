"""Tests for chunking and for summarization with a fake chat client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from openai import OpenAIError

from meeting_summarizer.exceptions import (
    ConfigurationError,
    EmptyTranscriptError,
    SummarizerError,
    UnsupportedMediaError,
)
from meeting_summarizer.summarize import chunk_transcript, summarize_file, summarize_transcript
from tests.fakes import FakeOpenAI


def _payload(summary: str, task: str, decision: str = "Ship on Monday") -> str:
    return json.dumps(
        {
            "summary": summary,
            "action_items": [{"task": task, "owner": "Ada", "due_date": "Friday"}],
            "key_decisions": [decision],
        }
    )


def test_short_text_is_one_chunk() -> None:
    assert chunk_transcript("  Standup notes.  ") == ["Standup notes."]
    assert chunk_transcript("   ") == []
    assert chunk_transcript("hello", chunk_size=5, overlap=0) == ["hello"]


def test_chunk_parameters_are_validated() -> None:
    with pytest.raises(ValueError):
        chunk_transcript("hello", chunk_size=0)
    with pytest.raises(ValueError):
        chunk_transcript("hello", chunk_size=20, overlap=20)
    with pytest.raises(ValueError):
        chunk_transcript("hello", chunk_size=20, overlap=-1)


def test_chunks_cover_paragraphs_and_overlap() -> None:
    paragraphs = [f"Paragraph {index} says {index} clearly." for index in range(8)]
    text = "\n\n".join(paragraphs)

    disjoint = chunk_transcript(text, chunk_size=80, overlap=0)
    assert len(disjoint) > 1
    assert all(len(chunk) <= 80 for chunk in disjoint)
    counts = {paragraph: sum(paragraph in chunk for chunk in disjoint) for paragraph in paragraphs}
    assert all(count == 1 for count in counts.values())

    overlapped = chunk_transcript(text, chunk_size=80, overlap=40)
    assert len(overlapped) > 1
    assert all(len(chunk) <= 80 for chunk in overlapped)
    for left, right in zip(overlapped, overlapped[1:]):
        shared = [paragraph for paragraph in paragraphs if paragraph in left and paragraph in right]
        assert shared


def test_long_unbroken_text_is_hard_wrapped() -> None:
    text = ("A" * 50) + ("B" * 50)
    chunks = chunk_transcript(text, chunk_size=40, overlap=0)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 40 for chunk in chunks)


def test_short_transcript_makes_one_chat_call() -> None:
    client = FakeOpenAI(completions=[_payload("The team agreed to ship.", "Email the notes")])
    summary = summarize_transcript(
        "Priya: We will ship on Monday. Ada will email the notes by Friday.",
        client=client,
        model="gpt-4o-mini",
    )

    assert summary.summary == "The team agreed to ship."
    assert summary.action_items[0].task == "Email the notes"
    assert summary.action_items[0].owner == "Ada"
    assert len(client.chat.completions.calls) == 1
    call = client.chat.completions.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["temperature"] == 0.2
    assert call["response_format"] == {"type": "json_object"}
    assert "extra_body" not in call
    assert "Transcript excerpt 1 of 1" in call["messages"][1]["content"]
    assert "Partial analyses" not in call["messages"][1]["content"]


def test_long_transcript_is_chunked_and_merged() -> None:
    paragraph = "The sync fix has to land before the staged rollout. " * 3
    text = "\n\n".join(f"{paragraph} Item {index}." for index in range(6))
    chunks = chunk_transcript(text, chunk_size=220, overlap=40)
    assert len(chunks) >= 2

    partials = [
        _payload(f"Part {index} summary.", f"Task {index}", f"Decision {index}")
        for index in range(len(chunks))
    ]
    merged = _payload("One combined summary.", "Task 0", "Decision 1")
    client = FakeOpenAI(completions=[*partials, merged])

    summary = summarize_transcript(
        text,
        client=client,
        model="gpt-4o",
        chunk_size=220,
        overlap=40,
    )

    calls = client.chat.completions.calls
    assert len(calls) == len(chunks) + 1
    assert all(call["model"] == "gpt-4o" for call in calls)
    assert "Transcript excerpt 1 of" in calls[0]["messages"][1]["content"]
    merge_input = calls[-1]["messages"][1]["content"]
    assert merge_input.startswith("Partial analyses")
    assert "Task 0" in merge_input
    assert f"Task {len(chunks) - 1}" in merge_input
    assert summary.summary == "One combined summary."
    assert summary.key_decisions == ["Decision 1"]


def test_fenced_and_invalid_json(tmp_path: Path) -> None:
    fenced = (
        '```json\n'
        '{"summary": "Done.", "action_items": [], "key_decisions": ["Ship"]}\n'
        '```'
    )
    client = FakeOpenAI(completions=[fenced])
    summary = summarize_transcript("A short meeting.", client=client, model="gpt-4o-mini")
    assert summary.summary == "Done."
    assert summary.key_decisions == ["Ship"]

    broken = FakeOpenAI(completions=["this is not json"])
    with pytest.raises(SummarizerError, match="valid JSON"):
        summarize_transcript("A short meeting.", client=broken, model="gpt-4o-mini")

    failing = FakeOpenAI(completions=[])

    def raise_timeout(**kwargs: object) -> None:
        raise OpenAIError("timeout")

    failing.chat.completions.create = raise_timeout
    with pytest.raises(SummarizerError, match="timeout"):
        summarize_transcript("A short meeting.", client=failing, model="gpt-4o-mini")


def test_groq_falls_back_after_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr("meeting_summarizer.config.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr("meeting_summarizer.retry.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("PROVIDER", "groq")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GROQ_FALLBACK_MODEL", raising=False)

    class Scripted:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.kwargs: list[dict[str, object]] = []

        def create(self, **kwargs: object) -> SimpleNamespace:
            model_name = str(kwargs["model"])
            self.calls.append(model_name)
            self.kwargs.append(kwargs)
            if model_name == "openai/gpt-oss-20b":
                error = Exception("429 rate limit")
                error.status_code = 429  # type: ignore[attr-defined]
                raise error
            content = _payload("Combined from the fallback.", "Send the notes")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    scripted = Scripted()
    client = SimpleNamespace(chat=SimpleNamespace(completions=scripted))
    summary = summarize_transcript("We decided to ship on Monday.", client=client)

    assert summary.summary == "Combined from the fallback."
    assert scripted.calls[0] == "openai/gpt-oss-20b"
    assert scripted.calls[-1] == "openai/gpt-oss-120b"
    assert scripted.calls.count("openai/gpt-oss-20b") == 5
    assert scripted.kwargs[-1]["extra_body"] == {
        "reasoning_effort": "low",
        "reasoning_format": "hidden",
        "max_completion_tokens": 2048,
    }


def test_empty_transcript_is_rejected() -> None:
    with pytest.raises(EmptyTranscriptError):
        summarize_transcript("  \n", client=FakeOpenAI(), model="gpt-4o-mini")


def test_summarize_file_reads_text_and_transcribes_audio(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("We decided to ship the checklist.", encoding="utf-8")
    text_client = FakeOpenAI(completions=[_payload("Shipping the checklist.", "Publish notes")])
    summary = summarize_file(notes, client=text_client, model="gpt-4o-mini")
    assert summary.action_items[0].task == "Publish notes"
    assert text_client.audio.transcriptions.calls == []
    assert "checklist" in text_client.chat.completions.calls[0]["messages"][1]["content"]

    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"fake-bytes")
    audio_client = FakeOpenAI(
        completions=[_payload("Talked through the launch.", "Open the branch")],
        transcript="Luis will open the branch by Thursday.",
    )
    summary = summarize_file(
        audio,
        client=audio_client,
        model="gpt-4o-mini",
        transcription_model="whisper-large-v3-turbo",
    )
    assert summary.summary == "Talked through the launch."
    assert audio_client.audio.transcriptions.calls[0]["model"] == "whisper-large-v3-turbo"
    assert "Thursday" in audio_client.chat.completions.calls[0]["messages"][1]["content"]


def test_summarize_file_rejects_bad_input_before_using_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("meeting_summarizer.config.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(FileNotFoundError):
        summarize_file(tmp_path / "missing.txt")

    empty = tmp_path / "empty.txt"
    empty.write_text(" ", encoding="utf-8")
    with pytest.raises(EmptyTranscriptError):
        summarize_file(empty)

    other = tmp_path / "slides.pdf"
    other.write_bytes(b"%PDF")
    with pytest.raises(UnsupportedMediaError):
        summarize_file(other)

    notes = tmp_path / "notes.txt"
    notes.write_text("Hello team.", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        summarize_file(notes, client=FakeOpenAI(), transcription_model="nope")
