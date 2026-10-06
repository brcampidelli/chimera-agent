"""Study 30, S30-23, after review: the integrity flags are about the attempt, not the verifier.

The autonomous loop computed the flags on the snapshot taken AFTER the verifier ran, so a verifier
that writes into the workspace (``jest`` outside ``--ci`` writes new
``__tests__/__snapshots__/*.snap``, and every path under ``__tests__`` is a test path) put
``tests_touched`` on an attempt that never touched a test. The Code tab measures before the
verifier; the two surfaces said different things under the same field name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.verify import VerificationResult


class _Worker:
    def __init__(self, ws: Path, writes: dict[str, str]) -> None:
        self.ws, self.writes = ws, writes

    def run(self, task: str) -> AgentResult:
        for rel, text in self.writes.items():
            (self.ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.ws / rel).write_text(text, encoding="utf-8")
        return AgentResult(answer="done", steps=1, transcript=[], stopped_reason="done")


class _SnapshotWritingVerifier:
    """Like ``jest`` outside ``--ci``: running the check writes a new snapshot file, and passes."""

    command = "npx jest"
    source = "user"

    def __init__(self, ws: Path) -> None:
        self.ws = ws

    def verify(self) -> VerificationResult:
        snap = self.ws / "__tests__" / "__snapshots__" / "a.test.js.snap"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text("exports[`a 1`] = `1`;\n", encoding="utf-8")
        return VerificationResult(True, "1 passed")


def _run(ws: Path, writes: dict[str, str]) -> Any:
    agent = AutonomousAgent(
        _Worker(ws, writes),  # type: ignore[arg-type]  # structural stand-in for the worker
        guard=WorkspaceGuard(ws),
        verifier=_SnapshotWritingVerifier(ws),  # type: ignore[arg-type]  # structural stand-in
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    return agent.run("fix it")


def test_a_file_the_verifier_writes_is_not_the_attempt_touching_tests(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.js").write_text("module.exports = 0\n", encoding="utf-8")
    result = _run(ws, {"a.js": "module.exports = 1\n"})
    assert result.success is True
    # The snapshot file is on disk — the verifier wrote it — but the attempt did not.
    assert (ws / "__tests__" / "__snapshots__" / "a.test.js.snap").exists()
    assert result.attempts[-1].integrity_flags == []


def test_a_test_the_attempt_itself_wrote_is_still_touched(tmp_path: Path) -> None:
    # The guard against the fix over-reaching: measuring before the verifier must not hide what
    # the worker did.
    ws = tmp_path / "ws"
    ws.mkdir()
    result = _run(ws, {"__tests__/b.test.js": "test('b', () => {})\n"})
    assert result.attempts[-1].integrity_flags == ["tests_touched: __tests__/b.test.js"]
