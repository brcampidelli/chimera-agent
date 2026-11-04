"""Helpers for opt-in media received from chat platforms."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from chimera.api.attachments import MAX_BYTES
from chimera.governance.ledger_tool import fence
from chimera.governance.sanitize import sanitize_untrusted
from chimera.server.voice import VOICE_MAX_SECONDS, FasterWhisperSpeech, VoiceTooLong

MEDIA_DISABLED_REPLY = "Voice and image messages are not enabled for this bot."
#: For media the bot accepts but would not read. Each failure used to answer with
#: MEDIA_DISABLED_REPLY, which told a person whose bot HAS media on that it was off (plan M6).
MEDIA_TOO_LARGE_REPLY = (
    "That file is larger than the 20 MB this bot accepts, so it was not read. "
    "Send a smaller one, or a link to it."
)
MEDIA_FAILED_REPLY = (
    "That voice note or image could not be downloaded, so it was not read. Try sending it again."
)


#: A voice note with no stated length, too big to take on trust (see ``VOICE_UNSTATED_MAX_BYTES``).
MEDIA_UNSTATED_LENGTH_REPLY = (
    "That voice note does not say how long it is and is too big to read without knowing, so it was "
    "not read. Send a shorter one."
)
#: A sender who has had their voice-note allowance for the window used up (``VoiceRateLimiter``).
VOICE_RATE_REPLY = "You have sent several voice notes in a row. Try again in a few minutes."


def too_long_reply(max_seconds: int) -> str:
    """What a chat is told when a voice note is longer than the owner's limit."""
    span = f"{max_seconds // 60} minutes" if max_seconds % 60 == 0 else f"{max_seconds} seconds"
    return f"That voice note is longer than {span}, so it was not read. Send a shorter one."


class Heard(str):
    """A transcript, already fenced as untrusted data, that also names the language it was spoken in.

    A ``str`` on purpose, as :class:`~chimera.server.gateway.Reply` is: the transcript is read as
    text everywhere, and the language is the one thing the voice reply needs beyond it.
    """

    language: str

    def __new__(cls, text: str, language: str = "") -> Heard:
        heard = super().__new__(cls, text)
        heard.language = language
        return heard


class MediaTooLarge(ValueError):
    """An inbound file over :data:`MAX_BYTES`, stopped before or during the download."""


def read_bounded(
    client: Any, url: str, *, headers: dict[str, str] | None = None, limit: int = MAX_BYTES,
) -> bytes:
    """Download ``url`` through ``client`` (httpx), stopping as soon as it passes :data:`MAX_BYTES`.

    Telegram and WhatsApp read the whole body first and measured it afterwards, so a file of any
    size was held in memory before it could be refused (plan M6). A declared length over the limit
    is refused before the first byte; an undeclared or understated one at the first chunk past it.
    """
    with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        declared = str(response.headers.get("content-length") or "")
        if declared.isdigit() and int(declared) > limit:
            raise MediaTooLarge(f"declared {declared} bytes")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > limit:
                raise MediaTooLarge(f"passed {limit} bytes")
            chunks.append(chunk)
    return b"".join(chunks)


def refusal_reply(exc: BaseException) -> str:
    """What a chat is told when its media was not read."""
    return MEDIA_TOO_LARGE_REPLY if isinstance(exc, MediaTooLarge) else MEDIA_FAILED_REPLY


def store_image(data: bytes, name: str) -> Path:
    """Store a bounded image in a temporary file for the provider's existing vision path."""
    if len(data) > MAX_BYTES:
        raise MediaTooLarge("inbound image exceeds the 20 MB limit")
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        suffix = ".png"
    with tempfile.NamedTemporaryFile(prefix="chimera-inbound-", suffix=suffix, delete=False) as handle:
        handle.write(data)
        return Path(handle.name)


def transcribe(path: Path, max_seconds: int = VOICE_MAX_SECONDS) -> Any:
    """Speech to text for a bot's voice note: faster-whisper on this computer, and nothing else.

    Not the ``transcribe_audio`` tool: that one falls back to a hosted API when the local model is
    missing, and a bot must not place a paid call for a stranger's voice note.
    """
    from chimera.config import get_settings

    models = get_settings().home / "models" / "whisper"
    return FasterWhisperSpeech(models).transcribe(path, max_seconds=max_seconds)


def transcribe_audio(data: bytes, name: str, *, max_seconds: int = VOICE_MAX_SECONDS) -> Heard:
    """Transcribe a voice note and fence its text. Raises :class:`VoiceTooLong` past ``max_seconds``."""
    if len(data) > MAX_BYTES:
        raise MediaTooLarge("inbound audio exceeds the 20 MB limit")
    suffix = Path(name).suffix.lower()
    if suffix not in {".wav", ".webm", ".ogg", ".mp3", ".m4a", ".flac"}:
        suffix = ".ogg"
    with tempfile.NamedTemporaryFile(prefix="chimera-inbound-", suffix=suffix, delete=False) as handle:
        handle.write(data)
        path = Path(handle.name)
    try:
        # A transcript from `transcribe` carries its length and language; a bare string (a test's
        # stand-in, or an older backend) carries neither, and is taken as the text alone.
        heard = transcribe(path, max_seconds=max_seconds)
        text = str(getattr(heard, "text", heard)).strip()
        language = str(getattr(heard, "language", "") or "")
        seconds = float(getattr(heard, "seconds", 0.0) or 0.0)
        if seconds > max_seconds:
            raise VoiceTooLong(f"{seconds:.0f} seconds is over the {max_seconds}-second limit")
        return Heard(fence(sanitize_untrusted(text)), language)
    finally:
        path.unlink(missing_ok=True)
