"""Expected failures raised by the meeting summarizer."""


class MeetingSummarizerError(Exception):
    """Base class for errors the CLI and web app can present directly."""


class MissingAPIKeyError(MeetingSummarizerError):
    """The OpenAI API key is not configured."""


class ConfigurationError(MeetingSummarizerError):
    """A model name or other setting is invalid."""


class UnsupportedMediaError(MeetingSummarizerError):
    """The input file type is not supported."""


class EmptyTranscriptError(MeetingSummarizerError):
    """A transcript or audio file had no usable content."""


class AudioTooLargeError(MeetingSummarizerError):
    """An audio file exceeds the transcription size limit."""


class SummarizerError(MeetingSummarizerError):
    """The chat model response could not be turned into a summary."""
