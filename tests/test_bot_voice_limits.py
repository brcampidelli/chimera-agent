"""The limits a voice note meets before, during and after it is read (review of the voice branch).

Each test uses a fake at a seam, as ``test_bot_voice.py`` does: a fake model whose length and
segments are controlled, a fake HTTP client, a fake attachment, a fake clock. No network, no model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from chimera.server import DiscordAdapter, TelegramAdapter
from chimera.server.gateway import Reply
from chimera.server.inbound_media import (
    MEDIA_UNSTATED_LENGTH_REPLY,
    VOICE_RATE_REPLY,
    MediaTooLarge,
    read_bounded,
)
from chimera.server.voice import (
    VOICE_UNSTATED_MAX_BYTES,
    FasterWhisperSpeech,
    VoiceRateLimiter,
    VoiceTooLong,
)

# --- 1. the length is read before any text is decoded ---------------------------------------------


class _Info:
    def __init__(self, duration: float, language: str = "pt") -> None:
        self.duration = duration
        self.language = language


class _Segment:
    def __init__(self, end: float) -> None:
        self.end = end
        self.text = f" words to {end}"


class _DecodingModel:
    """A stand-in for faster-whisper: ``transcribe`` returns the info at once and decodes lazily."""

    def __init__(self, duration: float, ends: list[float]) -> None:
        self.duration = duration
        self.ends = ends
        self.decoded: list[float] = []

    def transcribe(self, _path: str) -> tuple[Iterator[_Segment], _Info]:
        def segments() -> Iterator[_Segment]:
            for end in self.ends:
                self.decoded.append(end)
                yield _Segment(end)

        return segments(), _Info(self.duration)


def test_an_over_long_note_is_refused_before_a_single_segment_is_decoded(tmp_path: Path) -> None:
    model = _DecodingModel(duration=500.0, ends=[1.0, 2.0, 3.0])
    speech = FasterWhisperSpeech(tmp_path, model=model)
    with pytest.raises(VoiceTooLong):
        speech.transcribe(tmp_path / "a.ogg", max_seconds=300)
    assert model.decoded == []


def test_a_note_that_runs_past_the_limit_stops_decoding_at_that_segment(tmp_path: Path) -> None:
    # The stated length is inside the limit, but the audio runs past it: decoding stops at the
    # first segment that crosses the line, and the rest is never produced.
    model = _DecodingModel(duration=100.0, ends=[10.0, 20.0, 400.0, 500.0])
    with pytest.raises(VoiceTooLong):
        FasterWhisperSpeech(tmp_path, model=model).transcribe(tmp_path / "a.ogg", max_seconds=300)
    assert model.decoded == [10.0, 20.0, 400.0]


def test_a_note_inside_the_limit_is_transcribed_in_full(tmp_path: Path) -> None:
    model = _DecodingModel(duration=30.0, ends=[10.0, 20.0, 30.0])
    result = FasterWhisperSpeech(tmp_path, model=model).transcribe(tmp_path / "a.ogg", max_seconds=300)
    assert result.language == "pt" and result.seconds == 30.0
    assert model.decoded == [10.0, 20.0, 30.0]


# --- 2. a voice note whose length is not stated -------------------------------------------------


def _attachment(size: int, duration: float | None) -> Any:
    class Attachment:
        filename = "note.ogg"
        url = "https://cdn.invalid/a"

    item = Attachment()
    item.size = size  # type: ignore[attr-defined]
    item.duration = duration  # type: ignore[attr-defined]
    return item


def _inbound(adapter: DiscordAdapter, attachment: Any, author: str = "7") -> Any:
    return adapter._inbound(
        author_id=author, author_is_bot=False, is_self=False, channel_id="9", content="",
        attachments=[attachment],
    )


def test_unstated_audio_over_the_small_cap_is_refused_and_small_audio_is_listened_to() -> None:
    adapter = DiscordAdapter("t", inbound_media=True)
    big = _inbound(adapter, _attachment(size=20 * 1024 * 1024, duration=None))
    assert big is not None and big.media_refusal and big.media_refusal_reply == MEDIA_UNSTATED_LENGTH_REPLY
    small = _inbound(adapter, _attachment(size=VOICE_UNSTATED_MAX_BYTES - 1, duration=None))
    assert small is not None and small.media_kind == "audio" and not small.media_refusal


def test_a_stated_length_inside_the_limit_is_listened_to_at_any_declared_size_under_the_ceiling() -> None:
    adapter = DiscordAdapter("t", inbound_media=True)
    ok = _inbound(adapter, _attachment(size=15 * 1024 * 1024, duration=12.0))
    assert ok is not None and ok.media_kind == "audio" and ok.media_seconds == 12.0


def test_the_download_is_cut_mid_stream_at_the_limit() -> None:
    class Response:
        headers: dict[str, str] = {}

        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks
            self.pulled = 0

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> Iterator[bytes]:
            for chunk in self.chunks:
                self.pulled += 1
                yield chunk

    class Client:
        def __init__(self, response: Response) -> None:
            self.response = response

        @contextmanager
        def stream(self, _method: str, _url: str, **_kw: Any) -> Iterator[Response]:
            yield self.response

    response = Response([b"x" * 60] * 5)
    with pytest.raises(MediaTooLarge):
        read_bounded(Client(response), "https://cdn.invalid/a", limit=100)
    assert response.pulled == 2  # the second chunk crossed 100 bytes; nothing after it was read


# --- 5. the voice download: capped, redirects only to Hugging Face, .part removed -----------------



def test_discord_removes_the_spoken_reply_even_when_the_text_send_fails(tmp_path: Path) -> None:
    folder = tmp_path / "chimera-voice-x"
    folder.mkdir()
    audio = folder / "reply.ogg"
    audio.write_bytes(b"OggS")

    class Typing:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def send(_content: str) -> None:
        raise RuntimeError("discord is down")

    adapter = DiscordAdapter("t", allowed_users={"7"})
    with pytest.raises(RuntimeError):
        asyncio.run(adapter._respond(
            _voice_message(), lambda _m: Reply("answer", audio=audio), typing=Typing, send=send,
            send_audio=None,
        ))
    assert not folder.exists()


def _voice_message() -> Any:
    from chimera.server.gateway import InboundMessage

    return InboundMessage("", chat_id="9", platform="discord", user="7", media_kind="audio")


# --- 7. two first replies do not download the same voice twice, or share a .part file ----------


# --- 8. a per-sender allowance of voice notes ----------------------------------------------------


def test_the_rate_limiter_refuses_past_the_allowance_and_recovers_after_the_window() -> None:
    now = [0.0]
    limiter = VoiceRateLimiter(2, window=600.0, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")  # another sender has their own allowance
    now[0] = 601.0
    assert limiter.allow("a")


def test_a_zero_allowance_turns_the_limit_off() -> None:
    limiter = VoiceRateLimiter(0)
    assert all(limiter.allow("a") for _ in range(50))


def test_the_discord_bot_refuses_the_voice_note_past_the_allowance() -> None:
    adapter = DiscordAdapter("t", inbound_media=True, voice_per_user=1)
    first = _inbound(adapter, _attachment(size=1000, duration=5.0))
    second = _inbound(adapter, _attachment(size=1000, duration=5.0))
    assert first is not None and first.media_kind == "audio"
    assert second is not None and second.media_refusal and second.media_refusal_reply == VOICE_RATE_REPLY


def test_the_telegram_bot_refuses_an_unstated_length_note_over_the_small_cap() -> None:
    update = {"message": {"from": {"id": 7}, "chat": {"id": 9},
                          "voice": {"file_id": "f", "file_size": 20 * 1024 * 1024}}}
    refused = TelegramAdapter("t", inbound_media=True)._message_from_update(update)
    assert refused is not None and refused.media_refusal
    assert refused.media_refusal_reply == MEDIA_UNSTATED_LENGTH_REPLY


# --- the pin: the Whisper model loads at its commit ----------------------------------------------


