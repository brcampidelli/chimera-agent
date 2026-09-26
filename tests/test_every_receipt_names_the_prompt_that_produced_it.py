"""Every receipt names the system prompt that produced it — the fingerprint its trace line carries.

Study 25, wave 0, item 3. #606 gave each run's trace a `system_sha`: twelve hex characters of the
system message, so a change in behaviour could be set beside a change in the instructions. It
stopped at the trace. The receipts a person reads — under a Code-screen turn, and the run receipt
`GET /api/runs` returns — said which model answered, which route, what the cache served, and not
which instructions it was given. Two turns that behave differently could not be told apart as
"different prompt" or "same prompt, different luck" without opening `traces.jsonl` and joining by
hand.

Read off the step log, never recomputed: the trace line is written from the same object, so the
receipt and the trace cannot name two different prompts. And only the system message — the turn
context changes every turn, and a hash of it would differ between two turns given the same
instructions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core import Agent, AgentConfig, AutonomousAgent, AutonomousConfig
from chimera.core.verify import VerificationResult
from chimera.interface import ChatSession
from chimera.providers.gateway import CompletionResult
from chimera.tools import ToolRegistry


class _Model:
    """A model that answers at once, so a REAL agent loop runs, writes its trace and returns."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def complete(self, *_a: Any, **_k: Any) -> CompletionResult:
        return CompletionResult(
            content="done", model="fake/model", prompt_tokens=10, completion_tokens=1
        )


def _trace_shas(home: Path) -> list[str]:
    lines = (home / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["system_sha"] for line in lines if line.strip()]


def _frames(text: str) -> dict[str, dict[str, Any]]:
    """The last frame of each kind, by event name."""
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


# ------------------------------------------------------------------ the Code screen's turn


def _code_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    """The real Code turn — the product's own `Agent`, prompt and trace — on a model that answers.

    The gateway is the one thing replaced; the home is `tmp_path`, never the owner's."""
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", _Model)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(
        build_api_app(
            lambda: ChatSession(Agent(_Model(), ToolRegistry())), workspace=ws, settings=settings
        )
    )
    return client, home


def test_the_turn_receipt_carries_the_fingerprint_its_trace_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, home = _code_client(tmp_path, monkeypatch)
    response = client.post("/api/code/turn", json={"message": "say done", "stream": False})
    assert response.status_code == 200
    done = _frames(response.text)["done"]

    traced = _trace_shas(home)
    assert len(traced) == 1 and len(traced[0]) == 12
    assert done["system_sha"] == traced[0]

    # And on the receipt the conversation keeps, which is what a person reopens later.
    session_id = _frames(response.text)["session"]["session_id"]
    kept = client.get(f"/api/code/sessions/{session_id}").json()["exchanges"][-1]["done"]
    assert kept["system_sha"] == traced[0]


def test_two_system_prompts_give_two_receipts_and_a_turn_context_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spoken turn adds a note to the system message: a different prompt, a different value.
    Two typed turns of the same conversation differ in their turn context — the history, the
    question — and must NOT differ here, or the value would move on every turn and mean nothing."""
    client, home = _code_client(tmp_path, monkeypatch)
    first = client.post("/api/code/turn", json={"message": "say done", "stream": False})
    session_id = _frames(first.text)["session"]["session_id"]
    second = client.post(
        "/api/code/turn",
        json={"message": "and again, differently", "session_id": session_id, "stream": False},
    )
    spoken = client.post(
        "/api/code/turn",
        json={"message": "say done", "session_id": session_id, "stream": False, "spoken": True},
    )

    typed_a = _frames(first.text)["done"]["system_sha"]
    typed_b = _frames(second.text)["done"]["system_sha"]
    heard = _frames(spoken.text)["done"]["system_sha"]
    assert typed_a == typed_b
    assert heard != typed_a
    assert _trace_shas(home) == [typed_a, typed_b, heard]


# ------------------------------------------------------------------ the autonomous run


class _Passes:
    command = "true"

    def verify(self) -> VerificationResult:
        return VerificationResult(passed=True, output="ok")


def _run(home: Path, ws: Path, system_prompt: str) -> None:
    worker = Agent(
        _Model(),
        ToolRegistry(),
        AgentConfig(
            system_prompt=system_prompt,
            inject_skill_context=False,
            prefix_nonce="",
            trace_path=home / "traces.jsonl",
        ),
    )
    AutonomousAgent(
        worker,
        verifier=_Passes(),
        run_log=home / "runs.jsonl",
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    ).run("say done")


def test_the_run_receipt_carries_the_fingerprint_its_trace_carries_and_the_api_returns_it(
    tmp_path: Path,
) -> None:
    from chimera.api import build_api_app
    from chimera.api.runs import load_runs

    home, ws = tmp_path / "home", tmp_path / "ws"
    ws.mkdir()
    _run(home, ws, "You are a careful agent.")
    _run(home, ws, "You are a careless agent.")

    traced = _trace_shas(home)
    stored = [r.attempts[0].system_sha for r in load_runs(home / "runs.jsonl")]
    assert stored == traced
    assert len(set(stored)) == 2 and all(len(s) == 12 for s in stored)

    # Through the response model the Runs screen reads: a field missing from it is dropped on the
    # way out, and the receipt on disk would say something the screen never could.
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(
        build_api_app(lambda: ChatSession(Agent(_Model(), ToolRegistry())), settings=settings)
    )
    listed = client.get("/api/runs").json()
    assert [r["attempts"][0]["system_sha"] for r in listed] == list(reversed(traced))


def test_an_attempt_cut_short_by_the_cap_keeps_its_fingerprint(tmp_path: Path) -> None:
    """The spend cap and Stop record the attempt through their own path, not the verified one. That
    attempt called a model under some instructions, and its row must say which."""
    from chimera.api.runs import load_runs
    from chimera.core.agent import AgentResult
    from chimera.core.steplog import StepLog

    class _Capped:
        def run(self, task: str) -> AgentResult:
            return AgentResult(
                answer="spend cap reached", steps=1, stopped_reason="spend",
                steplog=StepLog(system_sha="0123456789ab"),
            )

    run_log = tmp_path / "runs.jsonl"
    AutonomousAgent(
        _Capped(),
        verifier=_Passes(),
        run_log=run_log,
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
    ).run("say done")
    (receipt,) = load_runs(run_log)
    assert [a.system_sha for a in receipt.attempts] == ["0123456789ab"]


def test_an_old_receipt_reads_as_not_recorded() -> None:
    """A row written before the field existed has no value to show, and says so with an empty
    string — never a fingerprint of something, which would name instructions nobody recorded."""
    from chimera.api.runs import RunReceipt

    old = RunReceipt.model_validate_json(
        '{"ts": "t", "task": "a", "attempts": [{"index": 1, "verified": true}]}'
    )
    assert old.attempts[0].system_sha == ""
