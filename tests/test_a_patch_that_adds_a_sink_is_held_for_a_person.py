"""The diff rule in the loop (`bench/test_gate_two_sided`, S2): an attempt whose change adds a sink
with a variable argument carries the flag on its receipt ALWAYS, and pauses the run for sign-off
ONLY on a surface that opted in — a REVIEW that a person resolves, never a BLOCK."""

from __future__ import annotations

from pathlib import Path

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.runstate import RunCheckpointer
from chimera.core.verify import VerificationResult

_PATCHED = '''"""A helper."""
import subprocess


def run_it(cmd):
    return subprocess.run(cmd, check=True)
'''


class _WritingWorker:
    """Writes a module whose new line calls a sink with a variable argument."""

    def __init__(self, ws: Path, text: str) -> None:
        self.ws, self.text, self.runs = ws, text, 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        (self.ws / "helper.py").write_text(self.text, encoding="utf-8")
        return AgentResult(answer="done", steps=1, transcript=[], stopped_reason="done")


class _PassVerifier:
    def verify(self) -> VerificationResult:
        return VerificationResult(True, "ok")


def _agent(ws: Path, store: RunCheckpointer, *, text: str = _PATCHED, pause: bool) -> AutonomousAgent:
    return AutonomousAgent(
        _WritingWorker(ws, text),  # type: ignore[arg-type]
        guard=WorkspaceGuard(ws), checkpointer=store, pause_on_diff_flags=pause,
        verifier=_PassVerifier(),  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )


def test_the_flag_is_on_the_receipt_even_when_nothing_pauses(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "helper.py").write_text('"""A helper."""\n', encoding="utf-8")
    result = _agent(ws, RunCheckpointer(tmp_path / "runs.db"), pause=False).run("t", thread_id="job")
    assert result.success is True and result.paused is False
    assert result.attempts[-1].diff_flags and "subprocess.run(cmd)" in result.attempts[-1].diff_flags[0]


def test_a_surface_that_opted_in_is_paused_with_the_flag_as_the_reason(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "helper.py").write_text('"""A helper."""\n', encoding="utf-8")
    store = RunCheckpointer(tmp_path / "runs.db")
    result = _agent(ws, store, pause=True).run("t", thread_id="job")
    assert result.paused is True and result.success is False
    saved = store.load("job")
    assert saved is not None and saved["awaiting_approval"] is True
    # The person approves; the reviewed answer is finalised without re-running the worker.
    assert store.approve("job") is True
    resumed = _agent(ws, store, pause=True).run("t", thread_id="job")
    assert resumed.success is True and resumed.paused is False


def test_a_literal_argument_neither_flags_nor_pauses(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "helper.py").write_text('"""A helper."""\n', encoding="utf-8")
    literal = _PATCHED.replace("subprocess.run(cmd, check=True)", 'subprocess.run(["ls"], check=True)')
    result = _agent(ws, RunCheckpointer(tmp_path / "runs.db"), text=literal, pause=True).run("t", thread_id="job")
    assert result.success is True and result.paused is False
    assert result.attempts[-1].diff_flags == []
