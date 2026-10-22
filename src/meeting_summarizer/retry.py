"""Retry provider calls that come back with HTTP 429."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 32.0


def is_rate_limit(exc: BaseException) -> bool:
    """Return whether ``exc`` is an HTTP 429 from a provider SDK."""
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def retry_after_seconds(exc: BaseException) -> float | None:
    """Read a ``Retry-After`` header, in seconds, when the provider sent one."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = None
    getter = getattr(headers, "get", None)
    if callable(getter):
        raw = getter("retry-after")
        if raw is None:
            raw = getter("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return seconds


def call_with_backoff(
    func: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    sleeper: Callable[[float], None] | None = None,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
) -> T:
    """Call ``func``, backing off when the provider returns 429.

    Other exceptions propagate immediately. The last 429 is re-raised after
    ``attempts`` tries. ``Retry-After`` wins over the exponential delay.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    sleep = sleeper or time.sleep
    delay = base_delay
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:
            if not is_rate_limit(exc) or attempt == attempts - 1:
                raise
            last = exc
            wait = retry_after_seconds(exc)
            if wait is None:
                wait = delay
                delay = min(delay * 2, max_delay)
            sleep(wait)
    assert last is not None
    raise last
