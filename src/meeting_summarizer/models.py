"""Structured meeting summary returned by the summarizer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from meeting_summarizer.exceptions import SummarizerError


def _blank_to_none(value: Any) -> str | None:
    """Normalize blank strings and null-like tokens to ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"null", "none", "n/a", "unknown"}:
            return None
        return stripped
    return str(value).strip() or None


class ActionItem(BaseModel):
    """A single follow-up committed to during the meeting."""

    task: str = Field(..., description="What needs to be done.")
    owner: str | None = Field(None, description="Person responsible, if named.")
    due_date: str | None = Field(
        None, description="Due date as stated in the meeting, if mentioned."
    )

    @field_validator("task")
    @classmethod
    def task_must_be_present(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("task must not be empty")
        return cleaned

    @field_validator("owner", "due_date", mode="before")
    @classmethod
    def empty_optional_is_none(cls, value: Any) -> str | None:
        return _blank_to_none(value)


class MeetingSummary(BaseModel):
    """Concise summary, action items, and decisions for one meeting."""

    summary: str = Field(..., description="Short narrative summary of the meeting.")
    action_items: list[ActionItem] = Field(default_factory=list)
    key_decisions: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def summary_must_be_present(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("summary must not be empty")
        return cleaned

    @field_validator("key_decisions")
    @classmethod
    def clean_decisions(cls, value: list[str]) -> list[str]:
        return [" ".join(item.split()) for item in value if item and item.strip()]

    def to_markdown(self) -> str:
        """Render the summary as Markdown suitable for notes or a pull request."""
        lines = ["# Meeting Summary", "", self.summary, "", "## Action Items", ""]
        if not self.action_items:
            lines.append("_None identified._")
        else:
            for item in self.action_items:
                meta: list[str] = []
                if item.owner:
                    meta.append(f"Owner: {item.owner}")
                if item.due_date:
                    meta.append(f"Due: {item.due_date}")
                suffix = f" — {'; '.join(meta)}" if meta else ""
                lines.append(f"- **{item.task}**{suffix}")
        lines.extend(["", "## Key Decisions", ""])
        if not self.key_decisions:
            lines.append("_None identified._")
        else:
            lines.extend(f"- {decision}" for decision in self.key_decisions)
        lines.append("")
        return "\n".join(lines)

    def to_json(self) -> str:
        """Serialize the summary as indented JSON."""
        return self.model_dump_json(indent=2) + "\n"


def _decision_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("decision", "text", "summary"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _action_item(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        value = {"task": value}
    if not isinstance(value, dict):
        return None
    task = value.get("task", value.get("action", value.get("item", "")))
    if not isinstance(task, str) or not task.strip():
        return None
    owner = value.get("owner", value.get("assignee", value.get("who")))
    due_date = value.get("due_date", value.get("due", value.get("deadline")))
    return {"task": task, "owner": owner, "due_date": due_date}


def meeting_summary_from_payload(payload: Any) -> MeetingSummary:
    """Validate a model JSON object and coerce common field aliases.

    Raises:
        SummarizerError: The payload is missing a summary or has the wrong shape.
    """
    if not isinstance(payload, dict):
        raise SummarizerError("The model response was not a JSON object.")

    raw_items = payload.get("action_items") or []
    if not isinstance(raw_items, list):
        raise SummarizerError("action_items must be a list.")
    items = [item for item in (_action_item(raw) for raw in raw_items) if item is not None]

    raw_decisions = payload.get("key_decisions") or []
    if not isinstance(raw_decisions, list):
        raise SummarizerError("key_decisions must be a list.")
    decisions = [text for text in (_decision_text(raw) for raw in raw_decisions) if text]

    summary = payload.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)

    try:
        return MeetingSummary(summary=summary, action_items=items, key_decisions=decisions)
    except ValueError as exc:
        raise SummarizerError(f"The model response could not be parsed: {exc}") from exc


def render_summary(summary: MeetingSummary, output_format: str) -> str:
    """Render a summary as ``markdown`` or ``json``."""
    if output_format == "json":
        return summary.to_json()
    if output_format == "markdown":
        return summary.to_markdown()
    raise SummarizerError(
        f"Unsupported output format '{output_format}'. Choose markdown or json."
    )
