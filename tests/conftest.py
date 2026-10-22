"""Shared fixtures for meeting-summarizer tests."""

from __future__ import annotations

import pytest

from meeting_summarizer.models import ActionItem, MeetingSummary


@pytest.fixture
def sample_summary() -> MeetingSummary:
    return MeetingSummary(
        summary="The team locked the October 26 staged rollout.",
        action_items=[
            ActionItem(
                task="Patch the sync worker",
                owner="Luis Ortega",
                due_date="Thursday, October 22",
            )
        ],
        key_decisions=["Android tablet support is deferred to next quarter."],
    )
