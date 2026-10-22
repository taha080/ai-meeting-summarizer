"""Chunk a transcript, summarize each part, and merge the results.

Short transcripts are sent to the chat model once. Longer transcripts are
split on paragraph boundaries with a small overlap, summarized independently,
and then combined in a final chat call so action items and decisions are
deduplicated.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError

from meeting_summarizer.config import (
    build_openai_client,
    resolve_chat_model,
    resolve_transcription_model,
)
from meeting_summarizer.exceptions import EmptyTranscriptError, SummarizerError
from meeting_summarizer.models import MeetingSummary, meeting_summary_from_payload
from meeting_summarizer.transcribe import is_audio_path, is_supported_path, load_transcript

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_CHARS = 12_000
DEFAULT_CHUNK_OVERLAP = 500

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")

_EXTRACT_SYSTEM = """You are an expert meeting analyst. Read the transcript excerpt and return a JSON object with exactly these keys:
- "summary": a concise paragraph covering only this excerpt
- "action_items": a list of objects with "task" (string), "owner" (string or null), and "due_date" (string or null)
- "key_decisions": a list of strings

Rules:
- Include only tasks, owners, dates, and decisions that the excerpt explicitly supports.
- Use null for owner or due_date when the excerpt does not state them.
- Do not invent people, dates, or commitments.
- Keep each action item and decision atomic.
- Return JSON only.
"""

_MERGE_SYSTEM = """You are an expert meeting analyst. You will receive JSON analyses of consecutive excerpts from the same meeting.
Combine them into one JSON object with exactly these keys:
- "summary": one concise paragraph describing the whole meeting
- "action_items": a deduplicated list of objects with "task", "owner", and "due_date"
- "key_decisions": a deduplicated list of strings

Rules:
- Merge items that describe the same task or decision. Keep the clearest wording and any owner or due date that was stated.
- Do not add information that is not present in the partial analyses.
- Use null when an owner or due date is unknown.
- Return JSON only.
"""


def chunk_transcript(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split ``text`` into chunks no longer than ``chunk_size`` characters.

    Paragraphs are kept intact when they fit. Consecutive chunks share a short
    tail so a commitment that lands on a boundary is still fully visible.
    A transcript that already fits is returned as a single stripped chunk.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")

    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    pieces = _pieces(cleaned, chunk_size)
    chunks: list[str] = []
    current: list[str] = []

    for piece in pieces:
        if current and len(_join(current)) + 2 + len(piece) > chunk_size:
            chunks.append(_join(current))
            current = _overlap_tail(current, overlap)
            if current and len(_join(current)) + 2 + len(piece) > chunk_size:
                current = []
        current.append(piece)

    if current:
        chunks.append(_join(current))
    return chunks


def summarize_transcript(
    transcript: str,
    *,
    client: OpenAI,
    model: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> MeetingSummary:
    """Summarize ``transcript`` with an OpenAI chat model.

    One chunk produces a single chat call. Several chunks produce one call per
    chunk plus a final call that merges the partial JSON analyses.
    """
    chat_model = resolve_chat_model(model)
    chunks = chunk_transcript(transcript, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        raise EmptyTranscriptError("The transcript is empty.")

    logger.info("Splitting transcript into %s chunk(s).", len(chunks))
    partials: list[MeetingSummary] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        payload = _complete_json(
            client,
            model=chat_model,
            system=_EXTRACT_SYSTEM,
            user=f"Transcript excerpt {index} of {total}:\n\n{chunk}",
        )
        partials.append(meeting_summary_from_payload(payload))

    if len(partials) == 1:
        return partials[0]

    logger.info("Merging %s partial summaries.", len(partials))
    merged_input = _merge_user_message(partials)
    merged = _complete_json(
        client,
        model=chat_model,
        system=_MERGE_SYSTEM,
        user=merged_input,
    )
    return meeting_summary_from_payload(merged)


def summarize_file(
    path: Path | str,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
    transcription_model: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> MeetingSummary:
    """Transcribe ``path`` when needed, then summarize the transcript.

    The OpenAI client is created from ``OPENAI_API_KEY`` when ``client`` is omitted.
    ``transcription_model`` is validated whenever the file is audio or a model
    was explicitly requested.
    """
    source = Path(path)
    explicit_transcription = bool(transcription_model and transcription_model.strip())
    if is_supported_path(source) and (is_audio_path(source) or explicit_transcription):
        transcription_model = resolve_transcription_model(transcription_model)

    return _summarize_loaded(
        source,
        client=client,
        model=model,
        transcription_model=transcription_model,
        chunk_size=chunk_size,
        overlap=overlap,
    )


def _summarize_loaded(
    source: Path,
    *,
    client: OpenAI | None,
    model: str | None,
    transcription_model: str | None,
    chunk_size: int,
    overlap: int,
) -> MeetingSummary:
    """Read or transcribe the file before creating a client for summarization.

    Transcription validates the audio file before it asks for an API key.
    When the caller does not pass a client, text files therefore fail fast
    on a missing or empty file without requiring ``OPENAI_API_KEY``.
    """
    transcript = load_transcript(
        source,
        client=client,
        transcription_model=transcription_model,
    )
    openai_client = client or build_openai_client()
    return summarize_transcript(
        transcript,
        client=openai_client,
        model=model,
        chunk_size=chunk_size,
        overlap=overlap,
    )


def _complete_json(
    client: OpenAI,
    *,
    model: str,
    system: str,
    user: str,
) -> dict[str, Any]:
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except OpenAIError as exc:
        raise SummarizerError(f"The summarization request failed: {exc}") from exc

    content = ""
    if response.choices:
        content = response.choices[0].message.content or ""
    return _parse_json_object(content)


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SummarizerError("The model did not return valid JSON.") from exc
    if not isinstance(payload, dict):
        raise SummarizerError("The model response was not a JSON object.")
    return payload


def _merge_user_message(partials: list[MeetingSummary]) -> str:
    blocks = []
    for index, partial in enumerate(partials, start=1):
        body = json.dumps(partial.model_dump(), indent=2, ensure_ascii=False)
        blocks.append(f"Part {index}:\n{body}")
    return "Partial analyses of one meeting:\n\n" + "\n\n".join(blocks)


def _join(parts: list[str]) -> str:
    return "\n\n".join(parts)


def _overlap_tail(parts: list[str], overlap: int) -> list[str]:
    """Keep a suffix of ``parts`` that fits inside ``overlap`` characters."""
    if overlap <= 0 or not parts:
        return []
    kept: list[str] = []
    total = 0
    for part in reversed(parts):
        separator = 2 if kept else 0
        needed = len(part) + separator
        if kept and total + needed > overlap:
            break
        if not kept and needed > overlap:
            return [part[-overlap:]]
        kept.append(part)
        total += needed
    kept.reverse()
    return kept


def _pieces(text: str, chunk_size: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= chunk_size:
            pieces.append(paragraph)
        else:
            pieces.extend(_split_long_paragraph(paragraph, chunk_size))
    return pieces


def _split_long_paragraph(paragraph: str, chunk_size: int) -> list[str]:
    sentences = [sentence.strip() for sentence in _SENTENCE_BREAK.split(paragraph) if sentence.strip()]
    if not sentences:
        sentences = [paragraph]
    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(sentence) > chunk_size:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.extend(_hard_wrap(sentence, chunk_size))
            continue
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if len(candidate) <= chunk_size:
            buffer = candidate
        else:
            if buffer:
                pieces.append(buffer)
            buffer = sentence
    if buffer:
        pieces.append(buffer)
    return pieces


def _hard_wrap(text: str, chunk_size: int) -> list[str]:
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]
