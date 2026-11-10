"""Voice for the chat bots, and every part of it runs on this computer.

Listening: a voice note a bot receives is transcribed by faster-whisper, locally only. The
``transcribe_audio`` tool falls back to the OpenAI Whisper API when faster-whisper is missing, and a
bot must never make that call. The Whisper model is downloaded on first use at a pinned commit.

Speaking: Chimera ships NO voice. A reply to a voice note comes back as a voice note only when the
owner names a local Piper voice (``CHIMERA_CHAT_VOICE_MODEL``, a ``.onnx`` file with its ``.onnx.json``
beside it). The owner chooses the voice and is responsible for its licence. Piper synthesises the
reply and ffmpeg encodes the OGG/Opus that Telegram and Discord take as audio. Nothing is downloaded
for speech. A missing or invalid model leaves the reply as text and logs why.

Licence: ``piper-tts`` 1.3 and later are GPL-3.0-or-later, so the ``voice`` extra is opt-in and is not
part of ``[full]``.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from chimera.proc.stdio import no_window
from chimera.telemetry import get_logger

_log = get_logger("server.voice")

#: The longest voice note a bot listens to, unless the owner sets otherwise.
VOICE_MAX_SECONDS = 300
#: How much of a long answer is spoken. The text itself is always sent in full.
SPOKEN_CHARS = 1500
#: A voice note whose length the platform does not state is only read below this size (1 MB is
#: a few minutes of speech at the bitrate the platforms use).
VOICE_UNSTATED_MAX_BYTES = 1024 * 1024
#: The largest voice model the owner may point at. A Piper voice is about 60 MB; this leaves room.
VOICE_MODEL_MAX_BYTES = 200 * 1024 * 1024
#: The largest voice config (``.onnx.json``) accepted. They are a few kilobytes.
VOICE_CONFIG_MAX_BYTES = 1024 * 1024
_CHAT_PREFIX = "chimera-voice-"

#: The Whisper models a bot may load, each at the commit of its Hugging Face repository that was
#: read on 2026-11-04. A size with no pin is loaded from ``main`` and logged as unpinned.
WHISPER_PINS: dict[str, tuple[str, str]] = {
    "base": ("Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"),
}


class VoiceUnavailable(RuntimeError):
    """The backend this needs is not installed, or the computer cannot run it."""


class VoiceTooLong(ValueError):
    """A voice note longer than the owner's limit."""


class Transcript(NamedTuple):
    text: str
    language: str
    seconds: float


#: What a long answer says in place of the rest of it, in each language.
CLOSING: dict[str, str] = {
    "pt": "O texto completo está acima.",
    "en": "The full text is above.",
}

_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_BULLET = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+", re.MULTILINE)
_MARKUP = re.compile(r"[*_`#>|~]+")


def speakable(text: str, language: str) -> str:
    """The answer as it should be heard: markup gone, and a long one cut at a sentence.

    A reply is written for a screen. Read aloud, a code block, a link or a stray asterisk is noise,
    so they are replaced by what they stand for. A reply longer than :data:`SPOKEN_CHARS` is spoken
    up to its last whole sentence and then pointed back to the text, which the chat has in full.
    """
    body = _CODE_BLOCK.sub(" ", text)
    body = _LINK.sub(r"\1", body)
    body = _URL.sub(" ", body)
    body = _BULLET.sub("", body)
    body = " ".join(_MARKUP.sub(" ", body).split())
    if len(body) <= SPOKEN_CHARS:
        return body
    head = body[:SPOKEN_CHARS]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    head = head[: end + 1] if end >= SPOKEN_CHARS // 2 else head.rsplit(" ", 1)[0]
    return f"{head} {CLOSING.get(language, CLOSING['en'])}".strip()


WHISPER_RETRY_SECONDS = 300.0
"""After a failed model download, the next note waits this long before trying again.

A bot that retries a download on every voice note sends the whole model's worth of traffic again for
each one, and a stranger can send many. The failure is cached for this long, then the download is
tried once more.
"""
_DOWNLOAD_FAILED_AT: dict[str, float] = {}


def whisper_snapshot(models_dir: Path, size: str) -> str:
    """The local folder of the Whisper model ``size``, downloaded at its pinned commit when needed.

    The first use downloads the model (about 150 MB for ``base``). The log says so before the download
    starts. A failed download is remembered for :data:`WHISPER_RETRY_SECONDS` and refused without a
    second attempt, so a bot does not re-download on every note.
    """
    import time

    from huggingface_hub import snapshot_download

    repo, revision = WHISPER_PINS.get(size, (f"Systran/faster-whisper-{size}", "main"))
    if size not in WHISPER_PINS:
        _log.warning("whisper model %s has no pinned revision; loading it from main", size)
    key = f"{models_dir}:{repo}@{revision}"
    failed_at = _DOWNLOAD_FAILED_AT.get(key)
    if failed_at is not None and time.monotonic() - failed_at < WHISPER_RETRY_SECONDS:
        raise VoiceUnavailable(
            "the speech model download failed a moment ago; the bot will try again in a few minutes"
        )
    try:
        return str(snapshot_download(
            repo_id=repo, revision=revision, cache_dir=str(models_dir), local_files_only=True,
        ))
    except Exception:  # noqa: BLE001 - not cached yet: the download below is the first use
        pass
    _log.warning(
        "downloading the speech model %s (about 150 MB) once, on first use; voice notes wait until it is here",
        repo,
    )
    try:
        return str(snapshot_download(repo_id=repo, revision=revision, cache_dir=str(models_dir)))
    except Exception as exc:
        _DOWNLOAD_FAILED_AT[key] = time.monotonic()
        _log.warning("speech model download failed: %s", type(exc).__name__)
        raise VoiceUnavailable("the speech model could not be downloaded") from exc


@lru_cache(maxsize=2)
def _pinned_model(models_dir: str, size: str) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(whisper_snapshot(Path(models_dir), size))


class FasterWhisperSpeech:
    """Listening, on this computer only: faster-whisper, with no hosted fallback.

    The length is read before any text is decoded, and the decoding stops as soon as a segment runs
    past the limit, so an over-long note costs the length check and not the whole transcription.
    """

    def __init__(self, models_dir: Path, *, model: Any = None) -> None:
        self.models_dir = models_dir
        self._injected = model

    def available(self) -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    def _model(self) -> Any:
        if self._injected is not None:
            return self._injected
        if not self.available():
            raise VoiceUnavailable("speech intake needs the stt extra: pip install 'chimera-agent[stt]'")
        return _pinned_model(str(self.models_dir), os.environ.get("CHIMERA_WHISPER_MODEL", "base"))

    def transcribe(self, path: Path, max_seconds: int = VOICE_MAX_SECONDS) -> Transcript:
        segments, info = self._model().transcribe(str(path))
        seconds = float(info.duration or 0.0)
        if seconds > max_seconds:
            raise VoiceTooLong(f"{seconds:.0f} seconds is over the {max_seconds}-second limit")
        parts: list[str] = []
        for segment in segments:
            if float(segment.end) > max_seconds:
                raise VoiceTooLong(f"the note runs past the {max_seconds}-second limit")
            parts.append(str(segment.text).strip())
        return Transcript(
            text=" ".join(parts).strip(), language=str(info.language or ""), seconds=seconds,
        )


def voice_model_problem(setting: str) -> tuple[Path | None, str]:
    """``(model, "")`` when the owner's setting names a usable local voice, else ``(None, reason)``.

    Checked on every reply, not once at start: the owner may move or replace the file, and a reply
    should say so rather than read a voice that is no longer there.
    """
    raw = (setting or "").strip()
    if not raw:
        return None, "no voice model is set (CHIMERA_CHAT_VOICE_MODEL)"
    if "://" in raw:
        return None, "the voice model must be a file on this computer, not a URL"
    # A UNC share (\host\share, //host/share) or a Win32 device path (\?\, \.\) is opened over the
    # network: is_file() would send a request to that host, and Windows answers it with the user's
    # NTLM credentials. Refused here, before any filesystem call.
    if raw.startswith(("\\", "//")):
        return None, "the voice model must be a local path, not a network share or device path"
    model = Path(raw).expanduser()
    if model.suffix.lower() != ".onnx":
        return None, "the voice model must be a .onnx file"
    if not model.is_file():
        return None, "the voice model file does not exist"
    config = model.with_name(model.name + ".json")
    if not config.is_file():
        return None, f"its config {config.name} is not beside it"
    if model.stat().st_size > VOICE_MODEL_MAX_BYTES:
        return None, "the voice model is larger than 200 MB"
    if config.stat().st_size > VOICE_CONFIG_MAX_BYTES:
        return None, "the voice config is larger than 1 MB"
    return model, ""


@lru_cache(maxsize=2)
def _piper_voice(model: str, config: str) -> Any:
    """The loaded voice, kept: loading an ONNX model is most of what a first reply costs."""
    from piper import PiperVoice

    return PiperVoice.load(model, config)


def encode_ogg(wav: Path, out: Path) -> None:
    """WAV to OGG/Opus with ffmpeg: the container and codec Telegram and Discord accept as audio."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise VoiceUnavailable("ffmpeg is not on PATH, and a voice note needs it to encode OGG")
    subprocess.run(  # noqa: S603 — fixed argv, both paths are ours
        [exe, "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", "-b:a", "32k", str(out)],
        check=True, timeout=120, capture_output=True, **no_window(),
    )


class PiperSpeech:
    """Speaking with the owner's local Piper voice: Piper synthesises, ffmpeg encodes."""

    def __init__(self, model_setting: str) -> None:
        self.model_setting = model_setting

    def problem(self) -> str:
        """Why speech is not available right now, or "" when it is."""
        if importlib.util.find_spec("piper") is None:
            return "the voice extra is not installed: pip install 'chimera-agent[voice]'"
        if shutil.which("ffmpeg") is None:
            return "ffmpeg is not on PATH"
        return voice_model_problem(self.model_setting)[1]

    def speak(self, text: str, language: str, out_dir: Path) -> Path:
        model, reason = voice_model_problem(self.model_setting)
        if model is None:
            raise VoiceUnavailable(reason)
        synth = _piper_voice(str(model), str(model.with_name(model.name + ".json")))
        wav = out_dir / "reply.wav"
        with wave.open(str(wav), "wb") as handle:
            synth.synthesize_wav(text, handle)
        ogg = out_dir / "reply.ogg"
        encode_ogg(wav, ogg)
        wav.unlink(missing_ok=True)
        return ogg


class Speech(Protocol):
    def problem(self) -> str: ...

    def speak(self, text: str, language: str, out_dir: Path) -> Path: ...


class VoiceRateLimiter:
    """How many voice notes one sender may have transcribed in a window; 0 turns the limit off.

    A bot that anyone can message would otherwise pay the computer's time for every note it is sent.
    The window slides, and a refusal costs nothing but the line that says so.
    """

    def __init__(
        self, per_user: int, *, window: float = 600.0, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_user = per_user
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, user: str) -> bool:
        if self.per_user <= 0:
            return True
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(user, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.per_user:
                return False
            hits.append(now)
            return True


class VoiceReplier:
    """Answers a voice note with a voice note beside the text. Every failure leaves the text alone."""

    def __init__(self, speech: Speech) -> None:
        self.speech = speech

    def __call__(self, answer: str, language: str) -> Path | None:
        reason = self.speech.problem()
        if reason:
            _log.warning("voice reply is off: %s; the reply went as text only", reason)
            return None
        text = speakable(answer, language)
        if not text:
            return None
        out_dir = Path(tempfile.mkdtemp(prefix=_CHAT_PREFIX))
        try:
            return self.speech.speak(text, language, out_dir)
        except Exception as exc:  # noqa: BLE001 — the text is the reply; a voice failure only loses audio
            _log.warning("voice reply failed, sent as text only: %s", type(exc).__name__)
            discard_reply_audio(out_dir / "reply.ogg")
            return None


def discard_reply_audio(path: Path) -> None:
    """Remove a voice reply once it has been sent (or not sent), with the folder made for it."""
    if path.parent.name.startswith(_CHAT_PREFIX):
        shutil.rmtree(path.parent, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def voice_reply_for(settings: Any, platform: str) -> VoiceReplier | None:
    """The spoken reply the owner switched on for this platform, or None.

    Only the two bots that send audio. The owner's voice model is read here and checked on every
    reply, so a missing or bad model is a logged text reply, never a broken bot.
    """
    if not getattr(settings, "chat_voice_reply", False) or platform not in {"discord", "telegram"}:
        return None
    return VoiceReplier(PiperSpeech(str(getattr(settings, "chat_voice_model", ""))))
