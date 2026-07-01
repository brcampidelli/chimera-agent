"""Tests for the reference tool library (image gen / TTS / email) — no network."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.tools.email import SendEmailTool
from chimera.tools.media import ImageGenTool, TextToSpeechTool


def test_image_gen_without_key_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    assert ImageGenTool().run(prompt="a cat").startswith("error:")


def test_image_gen_writes_decoded_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import httpx

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    payload = base64.b64encode(b"PNGDATA").decode()

    class Resp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, Any]:
            return {"data": [{"b64_json": payload}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    out = tmp_path / "img.png"
    result = ImageGenTool().run(prompt="a cat", out=str(out))
    assert out.read_bytes() == b"PNGDATA"
    assert "saved image" in result


def test_tts_without_key_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    get_settings.cache_clear()
    assert TextToSpeechTool().run(text="hi").startswith("error:")


def test_tts_writes_audio_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import httpx

    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    get_settings.cache_clear()

    class Resp:
        content = b"MP3BYTES"

        def raise_for_status(self) -> None: ...

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    out = tmp_path / "speech.mp3"
    result = TextToSpeechTool().run(text="hello", out=str(out))
    assert out.read_bytes() == b"MP3BYTES"
    assert "saved audio" in result


def test_send_email_without_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CHIMERA_SMTP_HOST", "CHIMERA_SMTP_USER", "CHIMERA_SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    assert SendEmailTool().run(to="a@b.com", subject="s", body="b").startswith("error:")


def test_send_email_via_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    import smtplib

    monkeypatch.setenv("CHIMERA_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("CHIMERA_SMTP_USER", "u@example.com")
    monkeypatch.setenv("CHIMERA_SMTP_PASSWORD", "secret")
    get_settings.cache_clear()
    seen: dict[str, Any] = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int) -> None:
            seen["host"] = host

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def starttls(self) -> None:
            seen["tls"] = True

        def login(self, user: str, password: str) -> None:
            seen["login"] = (user, password)

        def send_message(self, message: Any) -> None:
            seen["to"] = message["To"]

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    out = SendEmailTool().run(to="a@b.com", subject="hi", body="yo")
    assert out == "sent email to a@b.com"
    assert seen["tls"] is True and seen["login"] == ("u@example.com", "secret") and seen["to"] == "a@b.com"
