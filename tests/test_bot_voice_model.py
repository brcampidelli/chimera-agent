"""The owner's own voice: Chimera ships none, and a reply is spoken only with a valid local model.

No voice is downloaded and no model is loaded. Piper, ffmpeg and the encoder are fakes at the seams.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import types
import wave
from pathlib import Path
from typing import Any

import pytest

from chimera.api.bridge_routes import MESSAGING_SETTINGS
from chimera.api.config_api import ALLOWED_KEYS, APPLIES_WHEN, NEXT_LAUNCH, read_config
from chimera.config import Settings
from chimera.server import voice as voice_mod
from chimera.server.gateway import InboundMessage, MessageGateway, Reply
from chimera.server.voice import (
    WHISPER_PINS,
    PiperSpeech,
    VoiceReplier,
    discard_reply_audio,
    voice_model_problem,
    voice_reply_for,
)

KEY = "CHIMERA_CHAT_VOICE_MODEL"


@pytest.fixture
def ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Piper importable and ffmpeg on PATH, so only the model setting can be the problem."""
    real_find = importlib.util.find_spec

    def find(name: str, *args: Any, **kwargs: Any) -> Any:
        return types.SimpleNamespace() if name == "piper" else real_find(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find)
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **k: "/bin/ffmpeg" if name == "ffmpeg" else real_which(name, *a, **k),
    )


def _model(tmp_path: Path, name: str = "voice.onnx") -> Path:
    model = tmp_path / name
    model.write_bytes(b"onnx-bytes")
    model.with_name(model.name + ".json").write_text("{}", encoding="utf-8")
    return model


class FakeVoice:
    """Piper's ``PiperVoice``: writes a short silent WAV for whatever it is asked to say."""

    said: list[str] = []

    def synthesize_wav(self, text: str, handle: wave.Wave_write) -> None:
        self.said.append(text)
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 100)


def _fake_tts(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def load(model: str, config: str) -> FakeVoice:
        seen.update(model=model, config=config)
        return FakeVoice()

    def encode(wav: Path, out: Path) -> None:
        out.write_bytes(b"OggS-fake")

    monkeypatch.setattr(voice_mod, "_piper_voice", load)
    monkeypatch.setattr(voice_mod, "encode_ogg", encode)
    return seen


# --- no model set: text only -------------------------------------------------------------------------


def test_no_model_path_means_text_only_and_says_so(
    ready: None, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        assert VoiceReplier(PiperSpeech(""))("Olá.", "pt") is None
    assert "voice reply is off: no voice model is set" in caplog.text


def test_a_voice_note_with_the_toggle_on_and_no_model_is_answered_in_text(
    ready: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(voice_mod, "_piper_voice", lambda *_a: pytest.fail("no voice must be loaded"))
    class Agent:
        def run(self, task: str, *, images: Any = None) -> Any:
            return types.SimpleNamespace(answer="Feito.", tool_names=[])

    from chimera.interface import ChatSession

    session = ChatSession(Agent(), on_tainted_input=lambda _c: None)  # type: ignore[arg-type]
    gateway = MessageGateway(lambda: session, voice_reply=VoiceReplier(PiperSpeech("")))
    monkeypatch.setattr(
        "chimera.server.inbound_media.transcribe",
        lambda _p, **_k: types.SimpleNamespace(text="oi", language="pt", seconds=1.0),
    )
    reply = gateway.on_message(InboundMessage(
        "", chat_id="9", platform="telegram", user="7", media_kind="audio",
        media_data=b"ogg", media_name="voice.ogg", media_seconds=1.0,
    ))
    assert reply == "Feito." and not isinstance(reply, Reply)


# --- a valid model: audio ----------------------------------------------------------------------------


def test_a_valid_model_gives_an_audio_reply_from_the_owners_voice(
    ready: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model(tmp_path)
    seen = _fake_tts(monkeypatch)
    audio = VoiceReplier(PiperSpeech(str(model)))("Tudo certo por aqui.", "pt")
    assert audio is not None and audio.name == "reply.ogg" and audio.read_bytes() == b"OggS-fake"
    assert seen == {"model": str(model), "config": str(model) + ".json"}
    assert FakeVoice.said[-1] == "Tudo certo por aqui."
    discard_reply_audio(audio)
    assert not audio.exists()


def test_the_model_is_read_again_on_every_reply(
    ready: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model(tmp_path)
    _fake_tts(monkeypatch)
    replier = VoiceReplier(PiperSpeech(str(model)))
    first = replier("Um.", "pt")
    model.unlink()
    second = replier("Dois.", "pt")
    assert first is not None and second is None
    discard_reply_audio(first)


# --- a bad path: text only, with the reason logged ---------------------------------------------------


def _bad_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    wrong_suffix = tmp_path / "voice.txt"
    wrong_suffix.write_bytes(b"x")
    no_config = tmp_path / "solo.onnx"
    no_config.write_bytes(b"x")
    directory = tmp_path / "dir.onnx"
    directory.mkdir()
    big = _model(tmp_path, "big.onnx")
    monkeypatch.setattr(voice_mod, "VOICE_MODEL_MAX_BYTES", 1)
    return {
        "https://huggingface.co/x/voice.onnx": "must be a file on this computer",
        str(wrong_suffix): "must be a .onnx file",
        str(tmp_path / "missing.onnx"): "does not exist",
        str(no_config): "is not beside it",
        str(directory): "does not exist",
        str(big): "larger than 200 MB",
    }


def test_bad_model_paths_are_refused_with_a_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for setting, reason in _bad_cases(tmp_path, monkeypatch).items():
        model, problem = voice_model_problem(setting)
        assert model is None and reason in problem, (setting, problem)


def test_a_bad_path_replies_in_text_and_logs_why(
    ready: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    _fake_tts(monkeypatch)
    setting = str(tmp_path / "missing.onnx")
    with caplog.at_level("WARNING"):
        assert VoiceReplier(PiperSpeech(setting))("Olá.", "pt") is None
    assert "voice reply is off: the voice model file does not exist" in caplog.text


# --- the setting, the bridge, the screen -------------------------------------------------------------


def test_the_model_setting_defaults_empty_and_is_read_at_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY, raising=False)
    assert Settings().chat_voice_model == ""
    assert KEY in ALLOWED_KEYS and KEY in MESSAGING_SETTINGS and APPLIES_WHEN[KEY] == NEXT_LAUNCH


def test_the_save_check_refuses_a_bad_path_and_accepts_a_good_one(tmp_path: Path) -> None:
    from chimera.api.config_api import _VALUE_CHECKS

    check = _VALUE_CHECKS[KEY]
    check("")
    check(str(_model(tmp_path)))
    with pytest.raises(ValueError, match="does not exist"):
        check(str(tmp_path / "nope.onnx"))


def test_the_messaging_card_reads_the_model_path_back() -> None:
    body = read_config(Settings(CHIMERA_CHAT_VOICE_MODEL="/voices/me.onnx"))
    assert body["messaging"]["voice_model"] == "/voices/me.onnx"


def test_no_model_is_offered_without_the_reply_switch(tmp_path: Path) -> None:
    off = type("S", (), {"chat_voice_reply": False, "chat_voice_model": str(_model(tmp_path)), "home": tmp_path})()
    assert voice_reply_for(off, "telegram") is None


def test_the_whisper_model_is_fetched_at_its_pinned_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    seen: dict[str, str] = {}
    fake = types.ModuleType("huggingface_hub")

    def snapshot_download(repo_id: str, revision: str, cache_dir: str) -> str:
        seen.update(repo_id=repo_id, revision=revision)
        return str(tmp_path / "snap")

    fake.snapshot_download = snapshot_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    voice_mod.whisper_snapshot(tmp_path, "base")
    assert seen == {"repo_id": "Systran/faster-whisper-base", "revision": WHISPER_PINS["base"][1]}
    assert len(WHISPER_PINS["base"][1]) == 40


@pytest.mark.parametrize("setting", [
    r"\\host\share\x.onnx",       # UNC, backslashes
    "//host/share/x.onnx",        # UNC, slashes
    r"\\?\UNC\host\share\x.onnx",  # Win32 long-path UNC
    r"\\?\C:\voices\x.onnx",      # Win32 device path
    r"\\.\pipe\x.onnx",           # device namespace
])
def test_a_network_or_device_path_is_refused_before_any_filesystem_call(
    setting: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def touched(*_a: Any, **_k: Any) -> None:
        raise AssertionError(f"the filesystem was asked about {setting!r}")

    for name in ("is_file", "exists", "stat", "is_dir"):
        monkeypatch.setattr(Path, name, touched)
    model, problem = voice_model_problem(setting)
    assert model is None and "not a network share or device path" in problem


def test_the_save_check_refuses_a_unc_path_too(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.api.config_api import _VALUE_CHECKS

    monkeypatch.setattr(Path, "is_file", lambda *_a, **_k: pytest.fail("touched the filesystem"))
    with pytest.raises(ValueError, match="network share"):
        _VALUE_CHECKS[KEY](r"\\attacker\share\voice.onnx")
