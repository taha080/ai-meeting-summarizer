"""Tests for the free-tier token window."""

from __future__ import annotations

import pytest

from meeting_summarizer.budget import TokenBudget, estimate_tokens
from meeting_summarizer.exceptions import ConfigurationError


def test_estimate_overcounts_characters() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 2


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_budget_waits_for_the_next_minute() -> None:
    clock = _Clock()
    slept: list[float] = []

    def sleeper(seconds: float) -> None:
        slept.append(seconds)
        clock.now += seconds

    budget = TokenBudget(tokens_per_minute=8000, sleeper=sleeper, clock=clock)
    budget.acquire(5000)
    budget.acquire(4000)

    assert slept == [60.0]
    assert clock.now == 60.0


def test_budget_rejects_a_single_request_over_the_cap() -> None:
    budget = TokenBudget(tokens_per_minute=8000, sleeper=lambda _seconds: None, clock=lambda: 0.0)
    with pytest.raises(ConfigurationError, match="8000"):
        budget.acquire(8001)


def test_record_actual_replaces_the_reservation() -> None:
    clock = _Clock()
    budget = TokenBudget(tokens_per_minute=8000, sleeper=lambda _seconds: None, clock=clock)
    budget.acquire(3000)
    budget.record_actual(1200)
    budget.acquire(6000)
    assert clock.now == 0.0
