"""Voice for the chat bots: a voice note is heard locally, and a reply can come back as a voice note.

Every backend here is a fake injected at a seam: the transcriber is ``inbound_media.transcribe``,
the speech is a ``Speech`` object handed to ``VoiceReplier``, and the platform sends are fake
clients. No test downloads a voice, loads a model, or touches the network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from chimera.api.bridge_routes import MESSAGING_SETTINGS, OWNER_ONLY_SETTINGS
from chimera.api.config_api import ALLOWED_KEYS, APPLIES_WHEN, NEXT_LAUNCH
from chimera.config import Settings
from chimera.interface import ChatSession
from chimera.server import DiscordAdapter, TelegramAdapter
from chimera.server import voice as voice_mod
from chimera.server.gateway import InboundMessage, MessageGateway, Reply
from chimera.server.inbound_media import MEDIA_DISABLED_REPLY, too_long_reply
from chimera.server.voice import (
    CLOSING,
    SPOKEN_CHARS,
    PiperSpeech,
    Transcript,
    VoiceReplier,
    speakable,
    voice_reply_for,
)


class FakeSpeech:
    """A ``Speech`` that writes a fake OGG and records what it was asked to say."""

    def __init__(self, *, present: bool = True, fail: bool = False) -> None:
        self.present = present
        self.fail = fail
        self.said: list[tuple[str, str]] = []

    def problem(self) -> str:
        return "" if self.present else "the voice model is not set"

    def speak(self, text: str, language: str, out_dir: Path) -> Path:
        if self.fail:
            raise RuntimeError("synthesis broke")
        self.said.append((text, language))
        path = out_dir / "reply.ogg"
        path.write_bytes(b"OggS-fake")
        return path


class _Result:
    tool_names: list[str] = []

    def __init__(self, answer: str) -> None:
        self.answer = answer


class _Agent:
    def __init__(self, answer: str = "ok") -> None:
        self.answer = answer
        self.tasks: list[str] = []

    def run(self, task: str, *, images: list[str] | None = None) -> _Result:
        self.tasks.append(task)
        return _Result(self.answer)


def _stt(monkeypatch: pytest.MonkeyPatch, transcript: Transcript) -> None:
    from chimera.server import inbound_media

    monkeypatch.setattr(inbound_media, "transcribe", lambda _path, **_kw: transcript)


def _gateway(agent: _Agent, **kwargs: Any) -> MessageGateway:
    session = ChatSession(agent, on_tainted_input=lambda _c: None)  # type: ignore[arg-type]
    return MessageGateway(lambda: session, **kwargs)


def _voice(seconds: float = 4.0) -> InboundMessage:
    return InboundMessage(
        "", chat_id="9", platform="telegram", user="7", media_kind="audio",
        media_data=b"ogg-bytes", media_name="voice.ogg", media_seconds=seconds,
    )


# --- listening -------------------------------------------------------------------------------------


def test_voice_off_is_refused_with_the_disabled_line_and_no_download(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("chimera.server.inbound_media.transcribe", lambda _p: calls.append("x") or "")
    agent = _Agent()
    update = {"message": {"from": {"id": 7}, "chat": {"id": 9}, "voice": {"file_id": "f", "duration": 4}}}
    refused = TelegramAdapter("token")._message_from_update(update)
    assert refused is not None and refused.media_refusal and refused.media_refusal_reply == ""
    assert _gateway(agent).on_message(refused) == MEDIA_DISABLED_REPLY
    assert calls == [] and agent.tasks == []


def test_a_heard_voice_note_reaches_the_agent_as_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _stt(monkeypatch, Transcript(text="resuma o relatorio", language="pt", seconds=4.0))
    agent = _Agent(answer="Feito.")
    reply = _gateway(agent).on_message(_voice())
    assert reply == "Feito."
    task = agent.tasks[-1]
    assert "Untrusted audio transcript" in task and "resuma o relatorio" in task
    assert "<<external-data:" in task and "<<end-external-data>>" in task


def test_a_note_over_the_platform_length_is_refused_before_download() -> None:
    update = {"message": {"from": {"id": 7}, "chat": {"id": 9},
                          "voice": {"file_id": "f", "duration": 400}}}
    adapter = TelegramAdapter("token", inbound_media=True, voice_max_seconds=300)
    refused = adapter._message_from_update(update)
    assert refused is not None and refused.media_refusal and refused.media_kind == ""
    assert refused.media_refusal_reply == too_long_reply(300)
    assert "5 minutes" in refused.media_refusal_reply


def test_a_transcript_longer_than_the_limit_is_refused_and_not_routed(monkeypatch: pytest.MonkeyPatch) -> None:
    _stt(monkeypatch, Transcript(text="blah", language="pt", seconds=301.0))
    agent = _Agent()
    reply = _gateway(agent, voice_max_seconds=300).on_message(_voice(seconds=0.0))
    assert reply == too_long_reply(300)
    assert agent.tasks == []


def test_discord_refuses_a_long_voice_message_from_its_stated_length() -> None:
    class Voice:
        filename = "voice-message.ogg"
        url = "https://media.invalid/v"
        duration = 500.0

    args = dict(author_id="7", author_is_bot=False, is_self=False, channel_id="9", content="",
                attachments=[Voice()])
    refused = DiscordAdapter("t", inbound_media=True, voice_max_seconds=300)._inbound(**args)
    assert refused is not None and refused.media_refusal and refused.media_refusal_reply == too_long_reply(300)
    Voice.duration = 12.0
    ok = DiscordAdapter("t", inbound_media=True, voice_max_seconds=300)._inbound(**args)
    assert ok is not None and ok.media_kind == "audio" and ok.media_seconds == 12.0


def test_transcription_is_local_only_and_never_calls_the_hosted_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bots' listener must not fall back to the paid OpenAI route the transcribe tool uses."""
    import chimera.tools.media as media

    def hosted(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the hosted transcription route was called")

    monkeypatch.setattr(media, "_transcribe_openai", hosted)
    monkeypatch.setattr(voice_mod.FasterWhisperSpeech, "available", lambda self: False)
    from chimera.server.inbound_media import transcribe_audio

    with pytest.raises(voice_mod.VoiceUnavailable, match="stt extra"):
        transcribe_audio(b"ogg", "voice.ogg")


def test_the_transcript_language_is_carried_on_the_string(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.server.inbound_media import transcribe_audio

    _stt(monkeypatch, Transcript(text="oi", language="pt", seconds=1.0))
    heard = transcribe_audio(b"ogg", "voice.ogg")
    assert "oi" in heard and heard.language == "pt"


# --- speaking --------------------------------------------------------------------------------------


def test_voice_reply_off_is_text_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _stt(monkeypatch, Transcript(text="oi", language="pt", seconds=1.0))
    reply = _gateway(_Agent(answer="Olá.")).on_message(_voice(seconds=1.0))
    assert reply == "Olá." and not isinstance(reply, Reply)


def test_voice_reply_on_answers_a_voice_note_with_text_and_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    _stt(monkeypatch, Transcript(text="oi", language="pt", seconds=1.0))
    speech = FakeSpeech()
    gateway = _gateway(_Agent(answer="Olá, tudo bem?"), voice_reply=VoiceReplier(speech))
    reply = gateway.on_message(_voice(seconds=1.0))
    assert isinstance(reply, Reply) and reply == "Olá, tudo bem?"
    assert reply.audio is not None and reply.audio.name == "reply.ogg" and reply.audio.exists()
    assert speech.said == [("Olá, tudo bem?", "pt")]
    reply.audio.unlink()


def test_a_typed_message_gets_no_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    speech = FakeSpeech()
    gateway = _gateway(_Agent(answer="Olá."), voice_reply=VoiceReplier(speech))
    reply = gateway.on_message(InboundMessage("oi", chat_id="9", platform="telegram", user="7"))
    assert not isinstance(reply, Reply) and speech.said == []


def test_a_long_answer_is_spoken_in_part_and_sent_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    _stt(monkeypatch, Transcript(text="conte", language="pt", seconds=1.0))
    long_answer = "Esta é uma frase de teste bem comprida. " * 200
    speech = FakeSpeech()
    reply = _gateway(_Agent(answer=long_answer), voice_reply=VoiceReplier(speech)).on_message(_voice(1.0))
    spoken, language = speech.said[0]
    assert language == "pt"
    assert len(spoken) <= SPOKEN_CHARS + len(CLOSING["pt"]) + 1
    assert spoken.endswith("O texto completo está acima.")
    assert str(reply).startswith("Esta é uma frase") and len(str(reply)) > SPOKEN_CHARS
    reply.audio.unlink(missing_ok=True) if reply.audio else None


def test_missing_speech_leaves_the_reply_as_text_and_logs_the_hint(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    _stt(monkeypatch, Transcript(text="oi", language="pt", seconds=1.0))
    gateway = _gateway(_Agent(answer="Olá."), voice_reply=VoiceReplier(FakeSpeech(present=False)))
    with caplog.at_level("WARNING"):
        reply = gateway.on_message(_voice(1.0))
    assert reply == "Olá." and not isinstance(reply, Reply)
    assert "voice reply is off" in caplog.text and "not set" in caplog.text


def test_a_failing_synthesis_keeps_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _stt(monkeypatch, Transcript(text="oi", language="pt", seconds=1.0))
    gateway = _gateway(_Agent(answer="Olá."), voice_reply=VoiceReplier(FakeSpeech(fail=True)))
    reply = gateway.on_message(_voice(1.0))
    assert reply == "Olá." and not isinstance(reply, Reply)


def test_speakable_drops_markup_and_keeps_short_answers_whole() -> None:
    text = "## Plano\n- **um** passo\n```py\nprint(1)\n```\nveja [a doc](https://x.test/a)"
    spoken = speakable(text, "pt")
    assert "print" not in spoken and "http" not in spoken and "*" not in spoken and "#" not in spoken
    assert "a doc" in spoken and "um passo" in spoken
    assert speakable("Curta.", "pt") == "Curta."


def test_voice_reply_is_chosen_only_for_the_two_bots_the_owner_switched_on(tmp_path: Path) -> None:
    class S:
        chat_voice_reply = True
        chat_voice_model = ""
        home = tmp_path

    off = type("O", (), {"chat_voice_reply": False, "chat_voice_model": "", "home": tmp_path})()
    assert voice_reply_for(off, "telegram") is None
    assert voice_reply_for(S(), "whatsapp") is None
    assert isinstance(voice_reply_for(S(), "discord"), VoiceReplier)
    assert isinstance(voice_reply_for(S(), "telegram"), VoiceReplier)


# --- the voice files and the encoder ---------------------------------------------------------------


def test_piper_is_unavailable_without_ffmpeg_and_the_encoder_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(voice_mod.shutil, "which", lambda _name: None)
    assert PiperSpeech("").problem() != ""
    with pytest.raises(voice_mod.VoiceUnavailable, match="ffmpeg"):
        voice_mod.encode_ogg(tmp_path / "a.wav", tmp_path / "a.ogg")


def test_the_discarded_reply_takes_its_folder_with_it(tmp_path: Path) -> None:
    folder = tmp_path / "chimera-voice-abc"
    folder.mkdir()
    audio = folder / "reply.ogg"
    audio.write_bytes(b"x")
    voice_mod.discard_reply_audio(audio)
    assert not folder.exists()


# --- the platform sends ----------------------------------------------------------------------------


class _Response:
    def raise_for_status(self) -> None:
        return None


class _TelegramClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        return _Response()


def test_telegram_sends_the_spoken_reply_as_a_voice_note_and_deletes_it(tmp_path: Path) -> None:
    folder = tmp_path / "chimera-voice-t"
    folder.mkdir()
    audio = folder / "reply.ogg"
    audio.write_bytes(b"OggS")
    client = _TelegramClient()
    TelegramAdapter("token")._answer_with_voice(client, "9", Reply("hi", audio=audio))
    url, kwargs = client.calls[-1]
    assert url.endswith("/sendVoice") and kwargs["data"] == {"chat_id": "9"}
    assert kwargs["files"]["voice"][1] is not None
    assert not folder.exists()


def test_telegram_sends_nothing_for_a_text_reply() -> None:
    client = _TelegramClient()
    TelegramAdapter("token")._answer_with_voice(client, "9", "plain")
    assert client.calls == []


def test_discord_posts_the_spoken_reply_after_the_text_and_removes_it(tmp_path: Path) -> None:
    folder = tmp_path / "chimera-voice-d"
    folder.mkdir()
    audio = folder / "reply.ogg"
    audio.write_bytes(b"OggS")
    sent: list[Any] = []

    class Typing:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def send(content: str) -> None:
        sent.append(("text", content))

    async def send_audio(path: Path) -> None:
        sent.append(("audio", path.name))

    reply = Reply("answer", audio=audio)
    adapter = DiscordAdapter("t", allowed_users={"7"})
    asyncio.run(adapter._respond(
        _voice(), lambda _m: reply, typing=Typing, send=send, send_audio=send_audio,
    ))
    assert sent == [("text", "answer"), ("audio", "reply.ogg")]
    assert not folder.exists()


# --- settings --------------------------------------------------------------------------------------


def test_the_two_switches_and_the_length_default_off_and_are_read_at_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("CHIMERA_CHAT_VOICE_REPLY", "CHIMERA_CHAT_VOICE_MAX_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()
    assert settings.chat_voice_reply is False and settings.chat_voice_max_seconds == 300
    assert Settings(CHIMERA_CHAT_VOICE_REPLY=True).chat_voice_reply is True
    for key in ("CHIMERA_CHAT_INBOUND_MEDIA", "CHIMERA_CHAT_VOICE_REPLY", "CHIMERA_CHAT_VOICE_MODEL"):
        assert key in ALLOWED_KEYS
        assert key in OWNER_ONLY_SETTINGS and key in MESSAGING_SETTINGS
        assert APPLIES_WHEN[key] == NEXT_LAUNCH


def test_the_length_limit_must_be_a_positive_number_of_seconds() -> None:
    with pytest.raises(ValueError):
        Settings(CHIMERA_CHAT_VOICE_MAX_SECONDS=0)


def test_the_messaging_card_reads_both_switches_back() -> None:
    from chimera.api.config_api import read_config

    body = read_config(Settings(CHIMERA_CHAT_INBOUND_MEDIA=True, CHIMERA_CHAT_VOICE_REPLY=True))
    assert body["messaging"]["inbound_media"] is True and body["messaging"]["voice_reply"] is True
