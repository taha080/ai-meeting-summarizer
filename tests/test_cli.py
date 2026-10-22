"""Tests for the `summarize` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from meeting_summarizer.cli import app
from meeting_summarizer.models import MeetingSummary

runner = CliRunner()


def test_help_lists_format_and_out() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--format" in result.output
    assert "--out" in result.output
    assert "--provider" in result.output
    assert "markdown" in result.output
    assert "json" in result.output


def test_markdown_and_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_summary: MeetingSummary
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("ignored because the pipeline is patched", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_summarize(path: Path, **kwargs: object) -> MeetingSummary:
        seen["path"] = Path(path)
        seen["kwargs"] = kwargs
        return sample_summary

    monkeypatch.setattr("meeting_summarizer.cli.summarize_file", fake_summarize)

    markdown = runner.invoke(app, [str(notes), "--format", "markdown"])
    assert markdown.exit_code == 0
    assert "# Meeting Summary" in markdown.stdout
    assert "Patch the sync worker" in markdown.stdout
    assert seen["path"] == notes

    as_json = runner.invoke(
        app,
        [
            str(notes),
            "--format",
            "json",
            "--model",
            "gpt-4o",
            "--provider",
            "groq",
            "--transcription-model",
            "whisper-large-v3-turbo",
            "--chunk-size",
            "800",
        ],
    )
    assert as_json.exit_code == 0
    payload = json.loads(as_json.stdout)
    assert payload["summary"] == sample_summary.summary
    assert payload["key_decisions"] == sample_summary.key_decisions
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["model"] == "gpt-4o"
    assert kwargs["provider"] == "groq"
    assert kwargs["transcription_model"] == "whisper-large-v3-turbo"
    assert kwargs["chunk_size"] == 800


def test_out_writes_a_file_and_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_summary: MeetingSummary
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("hello", encoding="utf-8")
    destination = tmp_path / "reports" / "summary.md"
    monkeypatch.setattr(
        "meeting_summarizer.cli.summarize_file",
        lambda path, **kwargs: sample_summary,
    )

    result = runner.invoke(app, [str(notes), "--format", "markdown", "--out", str(destination)])

    assert result.exit_code == 0
    assert destination.is_file()
    assert destination.read_text(encoding="utf-8") == result.stdout
    assert "Patch the sync worker" in destination.read_text(encoding="utf-8")
    assert f"Wrote {destination}" in result.stderr


def test_missing_file_and_bad_format_fail(tmp_path: Path) -> None:
    missing = runner.invoke(app, [str(tmp_path / "missing.txt")])
    assert missing.exit_code != 0

    notes = tmp_path / "notes.txt"
    notes.write_text("hello", encoding="utf-8")
    bad_format = runner.invoke(app, [str(notes), "--format", "xml"])
    assert bad_format.exit_code != 0
    too_small = runner.invoke(app, [str(notes), "--chunk-size", "10"])
    assert too_small.exit_code != 0


def test_pipeline_errors_use_exit_code_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("hello", encoding="utf-8")

    def explode(path: Path, **kwargs: object) -> MeetingSummary:
        raise OSError("disk read failed")

    monkeypatch.setattr("meeting_summarizer.cli.summarize_file", explode)
    result = runner.invoke(app, [str(notes)])
    assert result.exit_code == 1
    assert "disk read failed" in result.stderr + result.stdout
