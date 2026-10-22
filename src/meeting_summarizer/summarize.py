"""Chunk a transcript, summarize each part, and merge the results.

Short transcripts are sent to the chat model once. Longer transcripts are
split on paragraph boundaries with a small overlap, summarized independently,
and then combined so action items and decisions are deduplicated.

The default provider is Groq's free tier. Chunks stay small enough for the
8,000 tokens-per-minute budget, and a 429 is retried with backoff. If
``openai/gpt-oss-120b`` fails, the call is tried again with
``llama-3.3-70b-versatile``.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError

from meeting_summarizer.budget import TokenBudget, estimate_tokens
from meeting_summarizer.config import (
    COMPLETION_RESERVE_TOKENS,
    DEFAULT_CHUNK_CHARS,
    DEFAULT_CHUNK_OVERLAP,
    FREE_TIER_TPM,
    build_client,
    resolve_chat_model,
    resolve_fallback_model,
    resolve_provider,
    resolve_transcription_model,
)
from meeting_summarizer.exceptions import (
    ConfigurationError,
    EmptyTranscriptError,
    ProviderRequestError,
    SummarizerError,
)
from meeting_summarizer.models import MeetingSummary, meeting_summary_from_payload
from meeting_summarizer.retry import call_with_backoff, is_rate_limit
from meeting_summarizer.transcribe import is_audio_path, is_supported_path, load_transcript

logger = logging.getLogger(__name__)

# Re-exported so the CLI can keep importing the chunk defaults from this module.
__all__ = ["DEFAULT_CHUNK_CHARS", "DEFAULT_CHUNK_OVERLAP", "summarize_file", "summarize_transcript"]

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
    provider: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    fallback_model: str | None = None,
) -> MeetingSummary:
    """Summarize ``transcript`` with the selected chat model.

    One chunk produces a single chat call. Several chunks produce one call per
    chunk, then merge calls. On Groq, requests wait so they stay inside
    8,000 tokens per minute, and the fallback model is used when the primary
    model keeps failing.
    """
    selected = resolve_provider(provider)
    chat_model = resolve_chat_model(model, provider=selected)
    fallback = fallback_model
    if fallback is None:
        configured_fallback = resolve_fallback_model(selected)
        default_model = resolve_chat_model(None, provider=selected)
        if (
            configured_fallback
            and chat_model == default_model
            and chat_model != configured_fallback
        ):
            fallback = configured_fallback
    chunks = chunk_transcript(transcript, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        raise EmptyTranscriptError("The transcript is empty.")

    budget = TokenBudget() if selected == "groq" else None
    logger.info("Splitting transcript into %s chunk(s).", len(chunks))
    partials: list[MeetingSummary] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        payload = _complete_json(
            client,
            model=chat_model,
            fallback_model=fallback,
            system=_EXTRACT_SYSTEM,
            user=f"Transcript excerpt {index} of {total}:\n\n{chunk}",
            budget=budget,
        )
        partials.append(meeting_summary_from_payload(payload))

    if len(partials) == 1:
        return partials[0]

    logger.info("Merging %s partial summaries.", len(partials))
    return _merge_partials(
        partials,
        client=client,
        model=chat_model,
        fallback_model=fallback,
        budget=budget,
    )


def summarize_file(
    path: Path | str,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
    transcription_model: str | None = None,
    provider: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> MeetingSummary:
    """Transcribe ``path`` when needed, then summarize the transcript.

    The client is created for the selected provider when ``client`` is omitted.
    Groq is the default and reads ``GROQ_API_KEY``. Text files are checked
    before a key is required.
    """
    source = Path(path)
    selected = resolve_provider(provider)
    explicit_transcription = bool(transcription_model and transcription_model.strip())
    if is_supported_path(source) and (is_audio_path(source) or explicit_transcription):
        transcription_model = resolve_transcription_model(
            transcription_model, provider=selected
        )

    return _summarize_loaded(
        source,
        client=client,
        model=model,
        transcription_model=transcription_model,
        provider=selected,
        chunk_size=chunk_size,
        overlap=overlap,
    )


def _summarize_loaded(
    source: Path,
    *,
    client: OpenAI | None,
    model: str | None,
    transcription_model: str | None,
    provider: str,
    chunk_size: int,
    overlap: int,
) -> MeetingSummary:
    """Read or transcribe the file before creating a client for summarization."""
    transcript = load_transcript(
        source,
        client=client,
        transcription_model=transcription_model,
        provider=provider,
    )
    openai_client = client or build_client(provider)
    return summarize_transcript(
        transcript,
        client=openai_client,
        model=model,
        provider=provider,
        chunk_size=chunk_size,
        overlap=overlap,
    )


def _merge_partials(
    partials: list[MeetingSummary],
    *,
    client: OpenAI,
    model: str,
    fallback_model: str | None,
    budget: TokenBudget | None,
) -> MeetingSummary:
    """Merge partial analyses, splitting the merge itself when it would exceed 8k tokens."""
    current = list(partials)
    while len(current) > 1:
        batches = _merge_batches(current)
        if len(batches) == 1:
            payload = _complete_json(
                client,
                model=model,
                fallback_model=fallback_model,
                system=_MERGE_SYSTEM,
                user=_merge_user_message(batches[0]),
                budget=budget,
            )
            return meeting_summary_from_payload(payload)
        merged: list[MeetingSummary] = []
        for batch in batches:
            if len(batch) == 1:
                merged.append(batch[0])
                continue
            payload = _complete_json(
                client,
                model=model,
                fallback_model=fallback_model,
                system=_MERGE_SYSTEM,
                user=_merge_user_message(batch),
                budget=budget,
            )
            merged.append(meeting_summary_from_payload(payload))
        if len(merged) >= len(current):
            raise ConfigurationError(
                "Partial summaries are too large to merge within the "
                f"{FREE_TIER_TPM} tokens-per-minute budget."
            )
        current = merged
    return current[0]


def _merge_batches(partials: list[MeetingSummary]) -> list[list[MeetingSummary]]:
    batches: list[list[MeetingSummary]] = []
    current: list[MeetingSummary] = []
    for partial in partials:
        trial = [*current, partial]
        if current and _request_tokens(_MERGE_SYSTEM, _merge_user_message(trial)) > _safe_request_tokens():
            batches.append(current)
            current = [partial]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def _safe_request_tokens() -> int:
    """Stay under the free-tier cap even when the tokenizer is less generous than our estimate."""
    return min(FREE_TIER_TPM - 500, 6500)


def _request_tokens(system: str, user: str) -> int:
    return estimate_tokens(system) + estimate_tokens(user) + COMPLETION_RESERVE_TOKENS


def _complete_json(
    client: OpenAI,
    *,
    model: str,
    system: str,
    user: str,
    budget: TokenBudget | None,
    fallback_model: str | None = None,
) -> dict[str, Any]:
    models = [model]
    if fallback_model and fallback_model != model:
        models.append(fallback_model)
    last_error: ProviderRequestError | None = None
    for index, model_name in enumerate(models):
        try:
            return _complete_once(
                client,
                model=model_name,
                system=system,
                user=user,
                budget=budget,
            )
        except ProviderRequestError as exc:
            last_error = exc
            if index == len(models) - 1:
                raise
            logger.warning("Model %s failed (%s). Trying %s.", model_name, exc, models[index + 1])
    assert last_error is not None
    raise last_error


def _complete_once(
    client: OpenAI,
    *,
    model: str,
    system: str,
    user: str,
    budget: TokenBudget | None,
) -> dict[str, Any]:
    estimated = _request_tokens(system, user)
    if budget is not None:
        budget.acquire(estimated)
    response = _create_chat(client, model=model, system=system, user=user)
    usage = getattr(response, "usage", None)
    total = getattr(usage, "total_tokens", None) if usage is not None else None
    if budget is not None and isinstance(total, int):
        budget.record_actual(total)
    content = ""
    if getattr(response, "choices", None):
        content = response.choices[0].message.content or ""
    return _parse_json_object(content)


def _create_chat(client: OpenAI, *, model: str, system: str, user: str) -> Any:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    def attempt(*, json_mode: bool) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": 0.2,
            "messages": messages,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    try:
        return call_with_backoff(lambda: attempt(json_mode=True))
    except Exception as exc:
        if _rejects_json_mode(exc):
            try:
                return call_with_backoff(lambda: attempt(json_mode=False))
            except Exception as retry_exc:
                exc = retry_exc
        if isinstance(exc, OpenAIError) or is_rate_limit(exc) or getattr(exc, "status_code", None):
            raise ProviderRequestError(f"The summarization request failed: {exc}") from exc
        raise


def _rejects_json_mode(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    return status in {400, 422} and "response_format" in text


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
