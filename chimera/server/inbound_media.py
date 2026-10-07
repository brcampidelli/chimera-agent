"""Helpers for opt-in media received from chat platforms."""

from __future__ import annotations

import tempfile
from pathlib import Path

from chimera.api.attachments import MAX_BYTES, transcribe
from chimera.governance.ledger_tool import fence
from chimera.governance.sanitize import sanitize_untrusted

MEDIA_DISABLED_REPLY = "Voice and image messages are not enabled for this bot."


def store_image(data: bytes, name: str) -> Path:
    """Store a bounded image in a temporary file for the provider's existing vision path."""
    if len(data) > MAX_BYTES:
        raise ValueError("inbound image exceeds the 20 MB limit")
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        suffix = ".png"
    with tempfile.NamedTemporaryFile(prefix="chimera-inbound-", suffix=suffix, delete=False) as handle:
        handle.write(data)
        return Path(handle.name)


def transcribe_audio(data: bytes, name: str) -> str:
    """Transcribe via the existing configured local/hosted tool and fence its untrusted result."""
    if len(data) > MAX_BYTES:
        raise ValueError("inbound audio exceeds the 20 MB limit")
    suffix = Path(name).suffix.lower()
    if suffix not in {".wav", ".webm", ".ogg", ".mp3", ".m4a", ".flac"}:
        suffix = ".ogg"
    with tempfile.NamedTemporaryFile(prefix="chimera-inbound-", suffix=suffix, delete=False) as handle:
        handle.write(data)
        path = Path(handle.name)
    try:
        text = transcribe(path).strip()
        if text.startswith("error:"):
            raise RuntimeError(text)
        return fence(sanitize_untrusted(text))
    finally:
        path.unlink(missing_ok=True)
