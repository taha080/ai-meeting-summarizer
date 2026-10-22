"""FastAPI application: one page to upload a meeting and read the summary.

Run it with ``meeting-web`` or ``uvicorn meeting_summarizer.web.app:app``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from meeting_summarizer.config import MAX_UPLOAD_BYTES, load_env
from meeting_summarizer.exceptions import (
    AudioTooLargeError,
    EmptyTranscriptError,
    MeetingSummarizerError,
    MissingAPIKeyError,
    SummarizerError,
    UnsupportedMediaError,
)
from meeting_summarizer.models import MeetingSummary
from meeting_summarizer.summarize import summarize_file
from meeting_summarizer.transcribe import SUPPORTED_EXTENSIONS

app = FastAPI(
    title="Meeting Summarizer",
    description="Turn meeting audio or a transcript into a summary, action items, and decisions.",
    version="0.1.0",
)

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"


@app.exception_handler(MeetingSummarizerError)
async def handle_summarizer_error(_request: object, exc: MeetingSummarizerError) -> JSONResponse:
    """Map expected failures onto HTTP status codes with a plain message."""
    status = 400
    if isinstance(exc, MissingAPIKeyError):
        status = 500
    elif isinstance(exc, SummarizerError):
        status = 502
    elif isinstance(exc, AudioTooLargeError):
        status = 413
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve the upload page."""
    if not _TEMPLATE.is_file():
        raise HTTPException(status_code=500, detail="The web template is missing.")
    return HTMLResponse(_TEMPLATE.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check that does not call a provider."""
    return {"status": "ok"}


@app.post("/api/summarize", response_model=MeetingSummary)
async def summarize_upload(
    file: UploadFile = File(..., description="MP3, WAV, M4A, or TXT meeting file."),
    model: str = Form(""),
    transcription_model: str = Form(""),
    provider: str = Form(""),
) -> MeetingSummary:
    """Accept an upload, summarize it, and return the structured result."""
    load_env()
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        shown = suffix or "unknown"
        raise UnsupportedMediaError(
            f"Unsupported file type '{shown}'. Upload an mp3, wav, m4a, or txt file."
        )

    directory = Path(tempfile.mkdtemp(prefix="meeting-summarizer-"))
    stored = directory / filename
    size = 0
    try:
        with stored.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    if MAX_UPLOAD_BYTES >= 1024 * 1024:
                        limit = f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                    else:
                        limit = f"{MAX_UPLOAD_BYTES} bytes"
                    raise AudioTooLargeError(f"The upload is larger than the {limit} limit.")
                handle.write(chunk)
        if size == 0:
            raise EmptyTranscriptError(f"{filename} is empty.")
        return summarize_file(
            stored,
            model=model.strip() or None,
            transcription_model=transcription_model.strip() or None,
            provider=provider.strip() or None,
        )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def main() -> None:
    """Run the upload UI at http://127.0.0.1:8000."""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
