"""Split recordings that are larger than a provider's upload cap.

Groq's free tier accepts 25 MB per transcription request. WAV files are split
with the standard library. MP3 and M4A are split with ffmpeg when it is
installed, after an optional 16 kHz mono re-encode that shrinks the file.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from meeting_summarizer.config import MAX_AUDIO_BYTES
from meeting_summarizer.exceptions import AudioTooLargeError

_HEADER_SLACK = 4096


def prepare_audio_segments(
    path: Path,
    *,
    max_bytes: int = MAX_AUDIO_BYTES,
) -> tuple[list[Path], Path | None]:
    """Return upload-sized pieces of ``path`` and a temp directory to delete.

    The temp directory is ``None`` when ``path`` already fits. Callers should
    delete the directory after the pieces have been transcribed.
    """
    path = Path(path)
    if path.stat().st_size <= max_bytes:
        return [path], None
    if path.suffix.lower() == ".wav":
        directory = Path(tempfile.mkdtemp(prefix="meeting-audio-"))
        return _split_wav(path, max_bytes=max_bytes, dest=directory), directory
    if shutil.which("ffmpeg"):
        directory = Path(tempfile.mkdtemp(prefix="meeting-audio-"))
        return _split_with_ffmpeg(path, max_bytes=max_bytes, dest=directory), directory
    raise AudioTooLargeError(
        f"{path.name} is larger than {max_bytes // (1024 * 1024)} MB. "
        "Install ffmpeg to split it, or export a smaller file. "
        "WAV files can be split without ffmpeg."
    )


def _split_wav(path: Path, *, max_bytes: int, dest: Path) -> list[Path]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        frame_rate = source.getframerate()
        frame_count = source.getnframes()
        frames = source.readframes(frame_count)
    frame_bytes = channels * sample_width
    if frame_bytes <= 0 or frame_rate <= 0 or frame_count <= 0:
        raise AudioTooLargeError(f"{path.name} could not be split because it has no audio frames.")
    slack = min(_HEADER_SLACK, max(128, max_bytes // 20))
    payload_budget = max_bytes - slack
    if payload_budget < frame_bytes:
        raise AudioTooLargeError(
            f"{path.name} cannot be split under {max_bytes} bytes."
        )
    frames_per_chunk = payload_budget // frame_bytes
    parts: list[Path] = []
    offset = 0
    index = 0
    while offset < frame_count:
        count = min(frames_per_chunk, frame_count - offset)
        piece = frames[offset * frame_bytes : (offset + count) * frame_bytes]
        target = dest / f"{path.stem}-{index:03d}.wav"
        with wave.open(str(target), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(sample_width)
            output.setframerate(frame_rate)
            output.writeframes(piece)
        if target.stat().st_size > max_bytes:
            raise AudioTooLargeError(f"A split of {path.name} is still larger than {max_bytes} bytes.")
        parts.append(target)
        offset += count
        index += 1
    if not parts:
        raise AudioTooLargeError(f"{path.name} could not be split.")
    return parts


def _split_with_ffmpeg(path: Path, *, max_bytes: int, dest: Path) -> list[Path]:
    encoded = _reencode_compact(path, dest / "compact.mp3")
    source = encoded if encoded is not None and encoded.stat().st_size < path.stat().st_size else path
    if source.stat().st_size <= max_bytes:
        return [source]
    duration = _duration_seconds(source)
    if duration is None or duration <= 0:
        raise AudioTooLargeError(
            f"{path.name} is larger than {max_bytes // (1024 * 1024)} MB "
            "and its duration could not be read for splitting."
        )
    segment_time = max(1.0, duration * (max_bytes * 0.85) / source.stat().st_size)
    pattern = dest / f"part%03d{source.suffix}"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-f",
            "segment",
            "-segment_time",
            f"{segment_time:.3f}",
            "-reset_timestamps",
            "1",
            "-c",
            "copy",
            str(pattern),
        ]
    )
    parts = sorted(dest.glob(f"part*{source.suffix}"))
    if not parts:
        raise AudioTooLargeError(f"{path.name} could not be split with ffmpeg.")
    if any(part.stat().st_size > max_bytes for part in parts):
        raise AudioTooLargeError(
            f"{path.name} is still over {max_bytes // (1024 * 1024)} MB after splitting."
        )
    return parts


def _reencode_compact(path: Path, target: Path) -> Path | None:
    """Re-encode speech to 16 kHz mono MP3 so a long recording fits the cap."""
    try:
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                "64k",
                str(target),
            ]
        )
    except AudioTooLargeError:
        return None
    if not target.is_file() or target.stat().st_size == 0:
        return None
    return target


def _duration_seconds(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            try:
                return float(completed.stdout.strip())
            except ValueError:
                return None
    return None


def _run_ffmpeg(command: list[str]) -> None:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise AudioTooLargeError(f"ffmpeg could not be started: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg failed").strip()
        last_line = detail.splitlines()[-1] if detail else "ffmpeg failed"
        raise AudioTooLargeError(last_line)
