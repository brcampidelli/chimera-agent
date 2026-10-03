"""A conversation's output style changes how the answer is written, and nothing else (study 29, P4.5).

The style chip on the Code screen picks one of three: default, concise, explanatory. Each is a fixed
suffix to the system prompt (`chimera/core/output_style.py`). Three promises are pinned here:

- the default is today's prompt byte for byte — a request without the field, and a request that
  names the default, build the same system message, and its receipt carries no style;
- a chosen style reaches the prompt and the receipt (with the version of its words), and nothing
  that decides what the turn may do — the tool registry is the same list either way;
- a spoken turn and an external agent's turn carry no style, so their receipts never claim one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.output_style import (
    CONCISE_NOTE,
    EXPLANATORY_NOTE,
    OUTPUT_STYLE_VERSION,
    OUTPUT_STYLES,
    style_suffix,
    with_output_style,
)
from chimera.interface import ChatSession

SYSTEM_PROMPTS: list[str] = []
TOOL_NAMES: list[list[str]] = []


class _Agent:
    """The API tests' fake agent, recording the system prompt and the tools each turn was built with."""

    def __init__(self, *args: Any, **_kw: Any) -> None:
        config = args[2] if len(args) > 2 else None
        if config is not None and hasattr(config, "system_prompt"):
            SYSTEM_PROMPTS.append(str(config.system_prompt))
        registry = args[1] if len(args) > 1 else None
        if registry is not None and hasattr(registry, "names"):
            TOOL_NAMES.append(sorted(registry.names()))

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
    TOOL_NAMES.clear()
    return TestClient(build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings))


def _frames(text: str) -> dict[str, dict[str, Any]]:
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


def _turn(client: TestClient, **body: Any) -> dict[str, dict[str, Any]]:
    response = client.post("/api/code/turn", json={"message": "explain the parser", **body})
    assert response.status_code == 200, response.text
    return _frames(response.text)


# ------------------------------------------------------------------ the default is today's prompt


def test_the_default_style_returns_the_very_prompt_it_was_given() -> None:
    base = "You are a coding agent."
    assert with_output_style(base, "default") is base
    assert with_output_style(base, None) is base
    assert style_suffix("default") == ""


def test_a_turn_with_no_style_and_a_turn_naming_the_default_build_the_same_system_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    before = _turn(client)
    named = _turn(client, style="default")

    assert len(SYSTEM_PROMPTS) == 2
    assert SYSTEM_PROMPTS[0] == SYSTEM_PROMPTS[1]
    assert CONCISE_NOTE not in SYSTEM_PROMPTS[0] and EXPLANATORY_NOTE not in SYSTEM_PROMPTS[0]
    # And the receipt is the receipt it always was: no style key at all, not "default".
    assert "style" not in before["done"] and "style_version" not in before["done"]
    assert "style" not in named["done"]


# ------------------------------------------------------------------ a chosen style


@pytest.mark.parametrize(("style", "note"), [("concise", CONCISE_NOTE), ("explanatory", EXPLANATORY_NOTE)])
def test_a_chosen_style_ends_the_system_prompt_and_is_named_on_the_receipt_with_its_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, style: str, note: str
) -> None:
    client = _client(tmp_path, monkeypatch)
    default = _turn(client)
    styled = _turn(client, style=style, session_id=default["session"]["session_id"])

    assert SYSTEM_PROMPTS[1] == f"{SYSTEM_PROMPTS[0]}\n\n{note}"
    assert styled["done"]["style"] == style
    assert styled["done"]["style_version"] == OUTPUT_STYLE_VERSION

    # Kept with the conversation, which is what a person reopens later.
    session_id = default["session"]["session_id"]
    kept = client.get(f"/api/code/sessions/{session_id}").json()["exchanges"]
    assert "style" not in kept[0]["done"]
    assert kept[1]["done"]["style"] == style


def test_a_style_never_enters_the_stored_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It rides in the system prompt, which the stored transcript drops — like the spoken note."""
    client = _client(tmp_path, monkeypatch)
    first = _turn(client, style="explanatory")
    stored = client.get(f"/api/code/sessions/{first['session']['session_id']}")
    assert stored.status_code == 200
    assert "Output style for this conversation" not in stored.text


def test_a_style_offers_the_turn_exactly_the_tools_the_default_offers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The style carries no permission: under the same posture the registry is the same list, for
    a read-only turn and for one that may edit, whichever style is picked."""
    client = _client(tmp_path, monkeypatch)
    for posture in ({"reach": "read_only", "approval": "suspicious"}, {"reach": "workspace", "approval": "suspicious"}):
        TOOL_NAMES.clear()
        for style in OUTPUT_STYLES:
            _turn(client, style=style, posture=posture)
        assert len(TOOL_NAMES) == len(OUTPUT_STYLES)
        assert all(names == TOOL_NAMES[0] for names in TOOL_NAMES), TOOL_NAMES


def test_an_unknown_style_is_refused_rather_than_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/code/turn", json={"message": "hi", "style": "pirate"})
    assert response.status_code == 422
    assert SYSTEM_PROMPTS == []


def test_each_suffix_says_it_changes_the_words_and_not_the_work() -> None:
    for note in (CONCISE_NOTE, EXPLANATORY_NOTE):
        assert "never what you do" in note
        assert "ask before risky actions" in note


# ------------------------------------------------------------------ where a style does not apply


def test_a_spoken_turn_keeps_the_voice_contract_and_carries_no_style(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.code_api import SPOKEN_NOTE

    client = _client(tmp_path, monkeypatch)
    spoken = _turn(client, style="explanatory", spoken=True)
    assert SYSTEM_PROMPTS[-1].endswith(SPOKEN_NOTE)
    assert EXPLANATORY_NOTE not in SYSTEM_PROMPTS[-1]
    assert "style" not in spoken["done"]


def test_an_external_agents_turn_is_never_suffixed() -> None:
    """Somebody else's agent runs its own prompt; a receipt naming our style there would describe
    words that agent was never given."""
    from chimera.api.code_api import CodeTurnRequest, _applied_style

    assert _applied_style(CodeTurnRequest(message="x", style="concise")) == "concise"
    assert _applied_style(CodeTurnRequest(message="x", style="concise", provider="claude")) is None
    assert _applied_style(CodeTurnRequest(message="x", style="concise", spoken=True)) is None
    assert _applied_style(CodeTurnRequest(message="x")) is None


# ------------------------------------------------------------------ the real loop's fingerprint


def test_on_the_real_loop_the_default_keeps_the_prompts_fingerprint_and_a_style_moves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The product's own `Agent` and prompt, on a model that answers at once: the `system_sha` the
    receipt carries is the same with no style and with the default, and a style changes it — which
    is how a reader tells "different words" from "same words, different luck"."""
    from chimera.api import build_api_app
    from chimera.config import get_settings
    from chimera.core import Agent
    from chimera.providers.gateway import CompletionResult
    from chimera.tools import ToolRegistry

    class _Model:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def complete(self, *_a: Any, **_k: Any) -> CompletionResult:
            return CompletionResult(content="done", model="fake/model", prompt_tokens=10, completion_tokens=1)

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", _Model)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(
        build_api_app(lambda: ChatSession(Agent(_Model(), ToolRegistry())), workspace=ws, settings=settings)
    )

    def sha(**body: Any) -> str:
        frames = _turn(client, stream=False, **body)
        return str(frames["done"]["system_sha"])

    plain = sha()
    assert sha(style="default") == plain
    concise = sha(style="concise")
    assert concise != plain
    assert sha(style="explanatory") not in {plain, concise}
