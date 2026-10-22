"""Tests for 429 backoff."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from meeting_summarizer.retry import call_with_backoff


def _rate_limit(message: str = "429 too many requests", retry_after: str | None = None) -> Exception:
    error = Exception(message)
    error.status_code = 429  # type: ignore[attr-defined]
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    error.response = SimpleNamespace(status_code=429, headers=headers)  # type: ignore[attr-defined]
    return error


def test_retries_rate_limits_then_returns() -> None:
    attempts = {"count": 0}
    slept: list[float] = []

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise _rate_limit(retry_after="2")
        return "ok"

    assert call_with_backoff(flaky, sleeper=slept.append) == "ok"
    assert attempts["count"] == 3
    assert slept == [2.0, 2.0]


def test_backoff_doubles_without_retry_after() -> None:
    slept: list[float] = []

    def always() -> None:
        raise _rate_limit("rate limit reached")

    with pytest.raises(Exception, match="rate limit"):
        call_with_backoff(always, sleeper=slept.append, attempts=4, base_delay=1, max_delay=10)
    assert slept == [1.0, 2.0, 4.0]


def test_other_errors_are_not_retried() -> None:
    attempts = {"count": 0}

    def broken() -> None:
        attempts["count"] += 1
        raise RuntimeError("bad request")

    with pytest.raises(RuntimeError, match="bad request"):
        call_with_backoff(broken, sleeper=lambda _seconds: None)
    assert attempts["count"] == 1
