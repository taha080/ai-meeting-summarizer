"""OpenAI stand-ins for tests. Nothing here talks to the network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class FakeCompletions:
    """Return scripted chat completion contents, one per ``create`` call."""

    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if not self._contents:
            raise AssertionError("No fake completion responses left")
        message = SimpleNamespace(content=self._contents.pop(0))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeTranscriptions:
    """Return a fixed transcript and record the audio request."""

    def __init__(self, text: str | Exception, *, as_string: bool = False) -> None:
        self._text = text
        self._as_string = as_string
        self.calls: list[dict[str, Any]] = []
        self.payload: bytes | None = None

    def create(self, **kwargs: Any) -> SimpleNamespace | str:
        self.calls.append(kwargs)
        uploaded = kwargs.get("file")
        if isinstance(uploaded, tuple) and len(uploaded) >= 2:
            handle = uploaded[1]
            position = handle.tell()
            self.payload = handle.read()
            handle.seek(position)
        if isinstance(self._text, Exception):
            raise self._text
        if self._as_string and isinstance(self._text, str):
            return self._text
        return SimpleNamespace(text=self._text)


class FakeOpenAI:
    """Minimal client with the ``audio`` and ``chat`` namespaces we call."""

    def __init__(
        self,
        completions: list[str] | None = None,
        transcript: str | Exception | None = None,
        *,
        transcript_as_string: bool = False,
    ) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(completions or []))
        self.audio = SimpleNamespace(
            transcriptions=FakeTranscriptions(
                "" if transcript is None else transcript,
                as_string=transcript_as_string,
            )
        )
