"""Study 30, S30-23, after review: the integrity rule is cheap where it cannot fire, and harmless.

`flag_snapshots` ran `difflib.unified_diff` over the full text of EVERY changed file, including
ordinary source that can raise no flag (not a test, not runner config, not named by the command).
On the Code tab that is new synchronous work on every editing turn — a quadratic diff of a large
generated file — and neither surface wrapped the call: an exception inside a record-only rule
would have taken the turn's verdict, or the attempt, down with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import chimera.governance.verifier_integrity as vi
from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.verify import VerificationResult


def _no_diff(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("diffed a file that cannot raise a flag")


def test_an_ordinary_source_file_is_never_diffed(monkeypatch: Any) -> None:
    monkeypatch.setattr(vi.difflib, "unified_diff", _no_diff)
    big = {"src/generated.py": "x = 1\n" * 5000}
    assert vi.flag_snapshots({}, big, verify_command="pytest tests") == []


def test_a_file_that_can_raise_a_flag_is_still_read(monkeypatch: Any) -> None:
    # The pre-filter must not hide the cases the rule exists for.
    flags = vi.flag_snapshots(
        {"check.sh": "exit 1\n", "tests/test_a.py": "def test_a():\n    assert f()\n"},
        {"check.sh": "exit 0\n", "tests/test_a.py": ""},
        verify_command="bash check.sh",
    )
    assert {f.kind for f in flags} == {vi.TESTS_TOUCHED, vi.VERIFIER_MODIFIED,
                                       vi.TESTS_REMOVED_OR_SKIPPED}


def _boom(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("integrity rule crashed")


class _Worker:
    def __init__(self, ws: Path) -> None:
        self.ws = ws

    def run(self, task: str) -> AgentResult:
        (self.ws / "m.py").write_text("x = 2\n", encoding="utf-8")
        return AgentResult(answer="done", steps=1, transcript=[], stopped_reason="done")


class _Pass:
    command, source = "pytest -q", "user"

    def verify(self) -> VerificationResult:
        return VerificationResult(True, "1 passed")


def test_a_crashing_integrity_rule_leaves_the_attempt_verdict_intact(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(vi, "flag_snapshots", _boom)
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    agent = AutonomousAgent(
        _Worker(tmp_path),  # type: ignore[arg-type]  # structural stand-in
        guard=WorkspaceGuard(tmp_path),
        verifier=_Pass(),  # type: ignore[arg-type]  # structural stand-in
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    result = agent.run("fix m")
    assert result.success is True
    assert result.attempts[-1].integrity_flags == []


def test_a_crashing_integrity_rule_leaves_the_code_turn_verdict_intact(
    tmp_path: Path, monkeypatch: Any
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("sse_starlette")
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.core.context_budget import RunState
    from chimera.interface import ChatSession

    class _Writes:
        def __init__(self) -> None:
            self.run_state = RunState()

        def run(self, task: str, *, on_edit: Any = None, **_: Any) -> AgentResult:
            (ws / "m.py").write_text("x = 2\n", encoding="utf-8")
            if on_edit:
                on_edit("m.py", "--- m.py\n+++ m.py\n@@\n+edit\n")
            return AgentResult(
                answer="done", steps=1, stopped_reason="final",
                transcript=[{"role": "user", "content": task},
                            {"role": "assistant", "content": "done"}],
                tool_names=[], model="test/model",
            )

    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    monkeypatch.setattr(vi, "flag_snapshots", _boom)
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "m.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "tests" / "test_it.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _Writes(), raising=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(build_api_app(lambda: ChatSession(_Writes()), workspace=ws,
                                      settings=settings))
    response = client.post("/api/code/turn", json={"message": "fix it"})
    event, verdict = "", {}
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: ") and event == "verified":
            verdict = json.loads(line[len("data: "):])
    assert verdict.get("state") == "passed", response.text[-2000:]
    assert verdict["integrity_flags"] == []
