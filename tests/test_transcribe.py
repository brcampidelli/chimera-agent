"""Tests for the speech-to-text tool — fakes only, no whisper/network."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chimera.governance.ledger import READ_TOOLS
from chimera.tools import media


def _audio(tmp_path: object) -> object:
    path = tmp_path / "clip.wav"  # type: ignore[operator]
    path.write_bytes(b"RIFF....WAVE")
    return path


def test_local_faster_whisper_path(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    _audio(tmp_path)
    monkeypatch.setattr(media, "_transcribe_faster_whisper", lambda p, lang: "the local transcript")
    out = media.TranscribeAudioTool(workspace=tmp_path).run(path="clip.wav")
    assert out == "the local transcript"


def test_hosted_whisper_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    _audio(tmp_path)
    monkeypatch.setattr(media, "_transcribe_faster_whisper", lambda p, lang: None)  # no stt extra
    monkeypatch.setattr(media, "_transcribe_openai", lambda p, key, lang: "the hosted transcript")
    monkeypatch.setattr(media, "get_settings", lambda: SimpleNamespace(key_pool=lambda prov: ["sk-1"]))
    out = media.TranscribeAudioTool(workspace=tmp_path).run(path="clip.wav")
    assert out == "the hosted transcript"


def test_no_backend_gives_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    _audio(tmp_path)
    monkeypatch.setattr(media, "_transcribe_faster_whisper", lambda p, lang: None)
    monkeypatch.setattr(media, "get_settings", lambda: SimpleNamespace(key_pool=lambda prov: []))
    out = media.TranscribeAudioTool(workspace=tmp_path).run(path="clip.wav")
    assert out.startswith("error:") and "stt" in out


def test_missing_file_is_error(tmp_path: object) -> None:
    assert media.TranscribeAudioTool(workspace=tmp_path).run(path="nope.wav").startswith("error:")


def test_transcribe_is_a_read_tool() -> None:
    assert "transcribe_audio" in READ_TOOLS
