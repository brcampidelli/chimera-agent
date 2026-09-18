"""The second pass on hands-free voice, from what its first live test found (2026-09-17).

Three things on the backend. A turn that arrived by voice tells the model so, in the system prompt
of that turn and nowhere else — the first live answer to a spoken "are you understanding me?" was
four paragraphs, two lists and an emoji, read aloud in full. The transcription endpoint takes the
app's language as a hint and runs off the event loop. And the local speech model is built once per
process: it was built on every call, and construction was most of every call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.code_api import SPOKEN_NOTE
from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

SYSTEM_PROMPTS: list[str] = []
#: The settings the last client was built with — injected, so frozen: a test that wants a setting
#: changed sets it on this object rather than through PATCH, which writes .env for a live app.
BUILT_WITH: list[Settings] = []


class _Agent:
    """The fake the API tests share, plus a record of the system prompt each turn was built with."""

    def __init__(self, *args: Any, **_kw: Any) -> None:
        config = args[2] if len(args) > 2 else None
        if config is not None and hasattr(config, "system_prompt"):
            SYSTEM_PROMPTS.append(str(config.system_prompt))

    def run(self, task: str, **kw: Any) -> AgentResult:
        history = list(kw.get("history") or [])
        return AgentResult(
            answer=f"answered: {task}",
            steps=1,
            stopped_reason="final",
            transcript=[
                *history,
                {"role": "user", "content": task},
                {"role": "assistant", "content": f"answered: {task}"},
            ],
            model="test/model",
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    SYSTEM_PROMPTS.clear()
    BUILT_WITH.append(settings)
    return TestClient(build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings))


def _frames(text: str) -> dict[str, dict[str, Any]]:
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


def test_a_spoken_turn_tells_the_model_to_answer_for_the_ear_and_a_typed_one_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    typed = client.post("/api/code/turn", json={"message": "are you there?"})
    assert typed.status_code == 200
    assert SYSTEM_PROMPTS and SPOKEN_NOTE not in SYSTEM_PROMPTS[-1]

    session_id = _frames(typed.text)["session"]["session_id"]
    spoken = client.post(
        "/api/code/turn", json={"message": "are you there?", "session_id": session_id, "spoken": True}
    )
    assert spoken.status_code == 200
    assert SPOKEN_NOTE in SYSTEM_PROMPTS[-1]
    # Where the screen part goes is in the note itself: the reader stops at that line.
    assert "line containing only ---" in SPOKEN_NOTE


def test_the_spoken_note_is_for_the_turn_and_never_enters_the_stored_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conversation is reopened, continued by typing, exported — the note belongs to none of
    that. It rides in the system prompt, which `absorb` drops, like the recalled facts do."""
    client = _client(tmp_path, monkeypatch)
    first = client.post("/api/code/turn", json={"message": "say hi", "spoken": True})
    session_id = _frames(first.text)["session"]["session_id"]

    stored = client.get(f"/api/code/sessions/{session_id}")
    assert stored.status_code == 200
    assert "read to them by a voice" not in stored.text

    # The next, typed turn of the same conversation is built without it.
    client.post("/api/code/turn", json={"message": "and now type", "session_id": session_id})
    assert SPOKEN_NOTE not in SYSTEM_PROMPTS[-1]


def test_transcription_passes_the_apps_language_as_a_hint_and_ignores_a_value_that_is_not_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.api.attachments as attachments

    client = _client(tmp_path, monkeypatch)
    calls: list[tuple[str, str | None]] = []

    def fake_transcribe(path: Path, language: str | None = None) -> str:
        calls.append((path.name, language))
        return "olá, tudo bem"

    monkeypatch.setattr(attachments, "transcribe", fake_transcribe)

    wav = ("file", ("speech.wav", b"RIFF....WAVEfmt ", "audio/wav"))
    with_hint = client.post("/api/transcribe", files=[wav], data={"language": "pt"})
    assert with_hint.status_code == 200 and with_hint.json()["text"] == "olá, tudo bem"
    without = client.post("/api/transcribe", files=[wav])
    assert without.status_code == 200
    nonsense = client.post("/api/transcribe", files=[wav], data={"language": "Portuguese (Brazil)"})
    assert nonsense.status_code == 200

    assert [language for _name, language in calls] == ["pt", None, None]


def test_the_local_speech_model_is_built_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Construction was most of a call — measured at 0.4–0.85 s against 0.18 s of transcription —
    and it happened on every call. The fake counts constructions; two transcriptions cost one."""
    import importlib.machinery
    import sys
    import types

    from chimera.tools import media

    built: list[str] = []

    class _Segment:
        text = " hello "

    class _WhisperModel:
        def __init__(self, size: str, **_kw: Any) -> None:
            built.append(size)

        def transcribe(self, _path: str, language: str | None = None) -> tuple[list[_Segment], Any]:
            return [_Segment()], types.SimpleNamespace(language=language or "en")

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = _WhisperModel  # type: ignore[attr-defined]
    fake.__spec__ = importlib.machinery.ModuleSpec("faster_whisper", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setenv("CHIMERA_WHISPER_MODEL", "tiny-test")
    media._whisper_model.cache_clear()

    assert media._transcribe_faster_whisper("a.wav", "pt") == "hello"
    assert media._transcribe_faster_whisper("b.wav", None) == "hello"
    assert built == ["tiny-test"]
    media._whisper_model.cache_clear()


# --------------------------------------------------------------------- thinking off


def test_a_spoken_turn_may_ask_the_model_not_to_think_first_and_only_through_openrouter() -> None:
    """Measured on 2026-09-17: the default model reasons for 2–15 s before its first visible
    word on a coding turn, and with reasoning off answers at 0.6–2.7 s. The switch travels as
    OpenRouter's unified parameter beside the route pin, merged rather than replacing it, and is
    not sent to a provider that would refuse it."""
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    gateway = LLMGateway(Settings())
    assert "extra_body" not in gateway._provider_kwargs("openrouter/deepseek/deepseek-v4-flash-0731")
    assert "extra_body" not in gateway._provider_kwargs("openrouter/deepseek/deepseek-v4-flash-0731", thinking=True)
    off = gateway._provider_kwargs("openrouter/deepseek/deepseek-v4-flash-0731", thinking=False)
    assert off["extra_body"] == {"reasoning": {"enabled": False}}
    # Beside a route pin, not instead of it.
    pinned = LLMGateway(Settings(CHIMERA_PROVIDER_ORDER="DeepSeek"))._provider_kwargs(  # type: ignore[call-arg]
        "openrouter/x/y", thinking=False
    )
    assert pinned["extra_body"] == {
        "provider": {"order": ["DeepSeek"], "allow_fallbacks": False},
        "reasoning": {"enabled": False},
    }
    # A provider without the parameter is called as before.
    assert "extra_body" not in gateway._provider_kwargs("openai/gpt-4.1-mini", thinking=False)
    assert "extra_body" not in gateway._provider_kwargs("ollama_chat/llama3", thinking=False)


def test_the_turn_request_carries_thinking_to_the_agent_only_when_it_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core

    seen: list[Any] = []

    class _Recording(_Agent):
        def __init__(self, *args: Any, **kw: Any) -> None:
            super().__init__(*args, **kw)
            if len(args) > 2:
                seen.append(getattr(args[2], "thinking", "missing"))

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(chimera.core, "Agent", _Recording, raising=True)
    client.post("/api/code/turn", json={"message": "hi"})
    client.post("/api/code/turn", json={"message": "hi", "spoken": True, "thinking": False})
    client.post("/api/code/turn", json={"message": "hi", "thinking": True})
    assert seen == [None, False, True]


def test_the_agent_passes_thinking_to_its_backend_only_when_set() -> None:
    """A backend that never heard of the flag is called exactly as before."""
    from chimera.core.agent import Agent, AgentConfig
    from chimera.providers.gateway import CompletionResult
    from chimera.tools.registry import ToolRegistry

    class _Backend:
        calls: list[dict[str, Any]] = []

        def complete(self, messages: Any, **kwargs: Any) -> CompletionResult:
            self.calls.append(kwargs)
            return CompletionResult(content="ok", model="m", prompt_tokens=1, completion_tokens=1)

    backend = _Backend()
    Agent(backend, ToolRegistry(), AgentConfig(max_steps=1)).run("q")
    Agent(backend, ToolRegistry(), AgentConfig(max_steps=1, thinking=False)).run("q")
    assert "thinking" not in backend.calls[0]
    assert backend.calls[1]["thinking"] is False


# --------------------------------------------------------------------- the voice model


def test_spoken_talk_goes_to_the_voice_model_without_thinking_and_spoken_work_to_the_conversations_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner tested a Gemini model live and heard the difference at the first word, then asked
    for two models: one to talk, one to reason when the request is work. `classify_task` is the
    split — a spoken question goes to the voice model with thinking off; a spoken "fix the login"
    goes to the conversation's model with its thinking; a typed turn is untouched."""
    import chimera.core

    seen: list[tuple[object, object]] = []

    class _Recording(_Agent):
        def __init__(self, *args: Any, **kw: Any) -> None:
            super().__init__(*args, **kw)
            if len(args) > 2:
                seen.append((getattr(args[2], "model", "missing"), getattr(args[2], "thinking", "missing")))

    def last() -> tuple[object, object]:
        return seen[-1]

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(chimera.core, "Agent", _Recording, raising=True)
    spoken = {"model": "openrouter/x/slow", "spoken": True, "thinking": False}

    # Nothing set: talk stays on the conversation's model, without thinking; work gets thinking back.
    client.post("/api/code/turn", json={"message": "o que é este projeto?", **spoken})
    assert last() == ("openrouter/x/slow", False)
    client.post("/api/code/turn", json={"message": "corrija o login", **spoken})
    assert last() == ("openrouter/x/slow", None)

    # The key is one Settings can take from the screen, and the snapshot shows it back. Owning the
    # name first (the PATCH exports it, and the suite's environment guard wants it put back), and
    # in a temp cwd: `patch_config` writes `.env` relative to the cwd, which is the developer's own
    # file when a test forgets this — it did, once, on 2026-09-18.
    monkeypatch.setenv("CHIMERA_VOICE_MODEL", "")
    monkeypatch.chdir(tmp_path)
    saved = client.patch("/api/config", json={"CHIMERA_VOICE_MODEL": "openrouter/google/gemini-2.5-flash-lite"})
    assert saved.status_code == 200
    monkeypatch.setattr(BUILT_WITH[-1], "voice_model", "openrouter/google/gemini-2.5-flash-lite")
    assert client.get("/api/config").json()["models"]["voice_model"] == "openrouter/google/gemini-2.5-flash-lite"

    client.post("/api/code/turn", json={"message": "o que é este projeto?", **spoken})
    assert last() == ("openrouter/google/gemini-2.5-flash-lite", False)
    client.post("/api/code/turn", json={"message": "me faça um teste para o login", **spoken})
    assert last() == ("openrouter/x/slow", None)
    client.post("/api/code/turn", json={"message": "corrija o login", "model": "openrouter/x/slow"})
    assert last() == ("openrouter/x/slow", None)
    client.post("/api/code/turn", json={"message": "o que é este projeto?", "model": "openrouter/x/slow"})
    assert last() == ("openrouter/x/slow", None)
    # A spoken question with no model chosen on the conversation still goes to the voice model.
    client.post("/api/code/turn", json={"message": "e o logout?", "spoken": True, "thinking": False})
    assert last() == ("openrouter/google/gemini-2.5-flash-lite", False)

    # The other half, chosen too: spoken work goes to the work model, thinking; talk is untouched.
    monkeypatch.setenv("CHIMERA_VOICE_WORK_MODEL", "")
    assert client.patch("/api/config", json={"CHIMERA_VOICE_WORK_MODEL": "openrouter/deepseek/deepseek-r1"}).status_code == 200
    monkeypatch.setattr(BUILT_WITH[-1], "voice_work_model", "openrouter/deepseek/deepseek-r1")
    assert client.get("/api/config").json()["models"]["voice_work_model"] == "openrouter/deepseek/deepseek-r1"
    client.post("/api/code/turn", json={"message": "refatore o módulo de login", **spoken})
    assert last() == ("openrouter/deepseek/deepseek-r1", None)
    client.post("/api/code/turn", json={"message": "o que é este projeto?", **spoken})
    assert last() == ("openrouter/google/gemini-2.5-flash-lite", False)
    client.post("/api/code/turn", json={"message": "refatore o módulo de login", "model": "openrouter/x/slow"})
    assert last() == ("openrouter/x/slow", None)
