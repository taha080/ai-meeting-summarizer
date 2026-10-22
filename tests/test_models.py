"""Tests for summary models and rendering."""

from __future__ import annotations

import json

import pytest

from meeting_summarizer.exceptions import SummarizerError
from meeting_summarizer.models import (
    ActionItem,
    MeetingSummary,
    meeting_summary_from_payload,
    render_summary,
)


def test_payload_coerces_blank_optional_fields_and_aliases() -> None:
    summary = meeting_summary_from_payload(
        {
            "summary": "  Shipped the plan.  ",
            "action_items": [
                {"task": " Email the notes ", "owner": "null", "due_date": ""},
                {"action": "File the bug", "assignee": "Luis", "deadline": "Friday"},
                "Write the recap",
                {"task": "   "},
            ],
            "key_decisions": ["  Ship on Monday  ", {"decision": "Defer tablets"}, ""],
        }
    )

    assert summary.summary == "Shipped the plan."
    assert summary.action_items[0] == ActionItem(task="Email the notes", owner=None, due_date=None)
    assert summary.action_items[1].owner == "Luis"
    assert summary.action_items[1].due_date == "Friday"
    assert summary.action_items[2].task == "Write the recap"
    assert len(summary.action_items) == 3
    assert summary.key_decisions == ["Ship on Monday", "Defer tablets"]


def test_payload_rejects_bad_shapes() -> None:
    with pytest.raises(SummarizerError):
        meeting_summary_from_payload(["not", "an", "object"])
    with pytest.raises(SummarizerError):
        meeting_summary_from_payload({"summary": "Ok", "action_items": "nope"})
    with pytest.raises(SummarizerError):
        meeting_summary_from_payload({"summary": "   ", "action_items": [], "key_decisions": []})


def test_markdown_and_json_rendering() -> None:
    summary = MeetingSummary(
        summary="Agreed to ship.",
        action_items=[ActionItem(task="Send notes", owner="José", due_date=None)],
        key_decisions=[],
    )

    markdown = summary.to_markdown()
    assert markdown.startswith("# Meeting Summary\n")
    assert "**Send notes** — Owner: José" in markdown
    assert "_None identified._" in markdown

    rendered = render_summary(summary, "json")
    payload = json.loads(rendered)
    assert payload["action_items"][0]["owner"] == "José"
    assert payload["action_items"][0]["due_date"] is None
    assert render_summary(summary, "markdown") == markdown

    with pytest.raises(SummarizerError):
        render_summary(summary, "xml")
