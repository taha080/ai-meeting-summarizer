"""Command-line interface for the meeting summarizer.

Installed as the ``summarize`` command::

    summarize meeting.mp3 --format markdown --out summary.md
"""

from __future__ import annotations

import logging
import sys
from enum import Enum
from pathlib import Path

import typer

from meeting_summarizer.config import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_TRANSCRIPTION_MODEL,
)
from meeting_summarizer.exceptions import MeetingSummarizerError
from meeting_summarizer.models import render_summary
from meeting_summarizer.summarize import DEFAULT_CHUNK_CHARS, summarize_file

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Summarize a meeting from audio or a plain-text transcript.",
)


class OutputFormat(str, Enum):
    """Formats the CLI can write."""

    markdown = "markdown"
    json = "json"


@app.command("summarize")
def summarize(
    source: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Audio file (.mp3, .wav, .m4a) or transcript (.txt).",
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.markdown,
        "--format",
        "-f",
        help="Output format.",
        show_default=True,
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Write the result to this file as well as standard output.",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help=f"groq (free, default), openai, or gemini. Default: PROVIDER or {DEFAULT_PROVIDER}.",
        show_default=False,
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=f"Chat model. Default for Groq: {DEFAULT_CHAT_MODEL}.",
        show_default=False,
    ),
    transcription_model: str | None = typer.Option(
        None,
        "--transcription-model",
        help=f"Audio model. Default for Groq: {DEFAULT_TRANSCRIPTION_MODEL}.",
        show_default=False,
    ),
    chunk_size: int = typer.Option(
        DEFAULT_CHUNK_CHARS,
        "--chunk-size",
        min=500,
        help="Max characters per chunk before the partial summaries are merged.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Print chunking progress to stderr.",
    ),
) -> None:
    """Summarize meeting audio or a plain-text transcript."""
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        summary = summarize_file(
            source,
            model=model,
            transcription_model=transcription_model,
            provider=provider,
            chunk_size=chunk_size,
        )
        rendered = render_summary(summary, output_format.value)
    except (MeetingSummarizerError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if out is not None:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Could not write {out}: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"Wrote {out}", err=True)

    sys.stdout.write(rendered)
    if not rendered.endswith("\n"):
        sys.stdout.write("\n")


def main() -> None:
    """Console-script entry point for ``summarize meeting.mp3``."""
    app()


if __name__ == "__main__":
    main()
