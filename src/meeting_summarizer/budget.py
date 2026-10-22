"""Keep Groq chat requests inside the free 8,000 tokens-per-minute budget."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from meeting_summarizer.config import CHARS_PER_TOKEN, FREE_TIER_TPM
from meeting_summarizer.exceptions import ConfigurationError


def estimate_tokens(text: str) -> int:
    """Estimate tokens from characters.

    Three characters per token over-estimates typical English, which keeps a
    chunk under Groq's real tokenizer instead of slipping past 8k TPM.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


class TokenBudget:
    """A rolling one-minute token window.

    ``acquire`` waits, when it has to, so the next request still fits.
    Pass ``sleeper`` and ``clock`` in tests so the wait is simulated.
    """

    def __init__(
        self,
        tokens_per_minute: int = FREE_TIER_TPM,
        *,
        window_seconds: float = 60.0,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if tokens_per_minute <= 0:
            raise ValueError("tokens_per_minute must be positive")
        self.tokens_per_minute = tokens_per_minute
        self.window_seconds = window_seconds
        self._sleep = sleeper or time.sleep
        self._clock = clock or time.monotonic
        self._events: list[tuple[float, int]] = []

    def acquire(self, tokens: int) -> None:
        """Reserve ``tokens`` or wait until the rolling window has room."""
        if tokens <= 0:
            return
        if tokens > self.tokens_per_minute:
            raise ConfigurationError(
                f"This request is about {tokens} tokens, which is above the free-tier "
                f"budget of {self.tokens_per_minute} tokens per minute. "
                "Use a smaller --chunk-size."
            )
        while True:
            now = self._clock()
            self._events = [
                (stamp, count)
                for stamp, count in self._events
                if now - stamp < self.window_seconds
            ]
            used = sum(count for _, count in self._events)
            if used + tokens <= self.tokens_per_minute:
                self._events.append((now, tokens))
                return
            oldest = min(stamp for stamp, _ in self._events)
            wait = self.window_seconds - (now - oldest)
            self._sleep(max(wait, 0.01))

    def record_actual(self, tokens: int) -> None:
        """Replace the latest reservation with the provider's usage count."""
        if tokens <= 0 or not self._events:
            return
        stamp, _reserved = self._events[-1]
        self._events[-1] = (stamp, tokens)
