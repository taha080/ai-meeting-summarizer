"""Tests for transcript loading and audio transcription."""

from __future__ import annotations

from pathlib import Path

import pytest
from openai import OpenAIError

from meeting_summarizer.exceptions import (
    AudioTooLargeError,
    EmptyTranscriptError,
    MissingAPIKeyError,
    SummarizerError,
    UnsupportedMediaError,
)
from meeting_summarizer.transcribe import load_transcript, transcribe_audio
from tests.fakes import FakeOpenAI

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_transcript.txt"


def test_text_file_is_read_locally_and_skips_audio(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("\ufeffWe agreed to ship on Monday.\n", encoding="utf-8")
    client = FakeOpenAI(transcript=AssertionError("audio should not be called"))

    assert load_transcript(notes, client=client) == "We agreed to ship on Monday."
    assert client.audio.transcriptions.calls == []


def test_example_transcript_loads() -> None:
    text = load_transcript(EXAMPLE)
    assert "Priya Shah" in text
    assert "October 26" in text


def test_transcribe_audio_sends_filename_and_model(tmp_path: Path) -> None:
    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"not-a-real-mp3")
    client = FakeOpenAI(transcript="Hello from the recording.")

    text = transcribe_audio(audio, client=client, model="whisper-large-v3-turbo")

    assert text == "Hello from the recording."
    call = client.audio.transcriptions.calls[0]
    assert call["model"] == "whisper-large-v3-turbo"
    name, _handle, content_type = call["file"]
    assert name == "talk.mp3"
    assert content_type == "audio/mpeg"
    assert client.audio.transcriptions.payload == b"not-a-real-mp3"


def test_transcribe_accepts_a_raw_string_response(tmp_path: Path) -> None:
    audio = tmp_path / "talk.wav"
    audio.write_bytes(b"RIFFfake")
    client = FakeOpenAI(transcript="plain text transcript", transcript_as_string=True)

    assert transcribe_audio(audio, client=client, model="whisper-large-v3-turbo") == "plain text transcript"
    assert client.audio.transcriptions.calls[0]["file"][2] == "audio/wav"


def test_m4a_content_type(tmp_path: Path) -> None:
    audio = tmp_path / "talk.m4a"
    audio.write_bytes(b"fake")
    client = FakeOpenAI(transcript="ok")
    transcribe_audio(audio, client=client, model="whisper-large-v3-turbo")
    assert client.audio.transcriptions.calls[0]["file"][2] == "audio/mp4"


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("notes.pdf", UnsupportedMediaError),
        ("notes", UnsupportedMediaError),
    ],
)
def test_unsupported_types(tmp_path: Path, name: str, error: type[Exception]) -> None:
    path = tmp_path / name
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(error):
        load_transcript(path)


def test_empty_and_missing_files(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(EmptyTranscriptError):
        load_transcript(empty)
    with pytest.raises(FileNotFoundError):
        load_transcript(tmp_path / "missing.txt")


def test_empty_and_oversized_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    with pytest.raises(EmptyTranscriptError):
        transcribe_audio(empty, client=FakeOpenAI(), model="whisper-large-v3-turbo")

    big = tmp_path / "big.mp3"
    big.write_bytes(b"123456")
    monkeypatch.setattr("meeting_summarizer.transcribe.MAX_AUDIO_BYTES", 4)
    with pytest.raises(AudioTooLargeError):
        transcribe_audio(big, client=FakeOpenAI(), model="whisper-large-v3-turbo")


def test_transcription_errors_and_blank_results(tmp_path: Path) -> None:
    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"abc")
    failing = FakeOpenAI(transcript=OpenAIError("upstream down"))
    with pytest.raises(SummarizerError, match="upstream down"):
        transcribe_audio(audio, client=failing, model="whisper-large-v3-turbo")

    silent = FakeOpenAI(transcript="   ")
    with pytest.raises(EmptyTranscriptError):
        transcribe_audio(audio, client=silent, model="whisper-large-v3-turbo")


def test_invalid_model_and_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"abc")
    with pytest.raises(Exception, match="Unsupported transcription model"):
        transcribe_audio(audio, client=FakeOpenAI(), model="nope")

    monkeypatch.setattr("meeting_summarizer.config.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("PROVIDER", raising=False)
    with pytest.raises(MissingAPIKeyError, match="GROQ_API_KEY"):
        transcribe_audio(audio, client=None, model="whisper-large-v3-turbo")


def test_openai_provider_uses_its_own_model(tmp_path: Path) -> None:
    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"abc")
    client = FakeOpenAI(transcript="from openai")
    text = transcribe_audio(audio, client=client, model="whisper-1", provider="openai")
    assert text == "from openai"
    assert client.audio.transcriptions.calls[0]["model"] == "whisper-1"


def test_large_wav_is_split_before_upload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import wave

    audio = tmp_path / "long.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x01" * 8000)
    monkeypatch.setattr("meeting_summarizer.transcribe.MAX_AUDIO_BYTES", 8000)
    client = FakeOpenAI(transcript="part")

    text = transcribe_audio(audio, client=client, model="whisper-large-v3-turbo")

    assert len(client.audio.transcriptions.calls) > 1
    assert text == "\n".join(["part"] * len(client.audio.transcriptions.calls))
    for call in client.audio.transcriptions.calls:
        assert call["file"][2] == "audio/wav"


def test_transcription_retries_rate_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    audio = tmp_path / "talk.mp3"
    audio.write_bytes(b"abc")
    monkeypatch.setattr("meeting_summarizer.retry.time.sleep", lambda _seconds: None)

    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls += 1
            if self.calls == 1:
                error = Exception("429 rate limit")
                error.status_code = 429  # type: ignore[attr-defined]
                raise error
            return SimpleNamespace(text="recovered")

    flaky = Flaky()
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=flaky))
    assert transcribe_audio(audio, client=client, model="whisper-large-v3-turbo") == "recovered"
    assert flaky.calls == 2


def test_transcribe_rejects_non_audio_suffix(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("hello", encoding="utf-8")
    with pytest.raises(UnsupportedMediaError):
        transcribe_audio(notes, client=FakeOpenAI(), model="whisper-large-v3-turbo")
