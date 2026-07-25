"""A Gym-style episode around one coding task — the verify-or-revert diff-gate as the reward (#3).

Chimera already has the pieces a reinforcement-learning environment needs — a task, a workspace, a
verifier, and a diff-gate that certifies a change was real — but they lived only inside the autonomous
loop. ``TaskEnv`` exposes the standard ``reset()`` / ``step()`` / ``state()`` contract over them, so
the SAME substrate serves two ends: (a) replaying a benchmark deterministically, and (b) being the
environment a future agentic-RL trainer (e.g. verl-agent) drives — where Chimera's structural
advantage is that it *already owns the verifiable reward* (the diff-gate) that such training otherwise
has to invent.

Reward = the diff-gate: 1.0 only when the workspace actually changed (no reward for a hollow no-op)
AND the verifier passes; 0.0 otherwise — exactly the ``learn = productive is not False`` + verify rule
the autonomous loop uses. The episode is terminal in one step because verify-or-revert is a terminal
signal; multi-step tool-use episodes are a future extension, not modelled here.

The verifier is injectable, so the whole env is testable without a network or a real model (mirroring
``chimera.eval.paired``). The pristine test is restored before grading, so a policy cannot become its
own judge by editing the test — the same grading-integrity rule the learning-lift bench enforces.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

# A verifier decides pass/fail for a prepared workspace. Injectable so tests need no subprocess.
Verifier = Callable[[Path], bool]


@dataclass
class EnvState:
    """The observation returned by ``reset``/``step``/``state``."""

    task: str  # the task prompt (what the policy is asked to solve)
    workspace: Path  # the live workspace directory
    files: dict[str, str]  # current contents of the SOLUTION files (the test is not an action target)
    done: bool = False
    reward: float = 0.0
    info: dict[str, object] = field(default_factory=dict)


class TaskEnv:
    """One coding task as a single-step episode; reward is the verify-or-revert diff-gate.

    ``task`` is the learning-lift / local_lift task shape: a mapping with ``id``, ``prompt``,
    ``files`` (relative path -> starter content), ``test`` (test filename), ``test_src`` (committed
    test content), and optionally ``verify`` (a shell command; defaults to pytest on ``test``).
    """

    def __init__(
        self,
        task: Mapping[str, object],
        *,
        root: Path,
        verifier: Verifier | None = None,
        timeout: int = 120,
    ) -> None:
        self.task = dict(task)
        self.root = Path(root)
        self.timeout = timeout
        self._verifier = verifier  # None -> the default pytest verifier
        self._state: EnvState | None = None

    # -- the standard contract --------------------------------------------------
    def reset(self) -> EnvState:
        """Lay out a fresh workspace (starter files + the committed test) and return the observation."""
        ws = self.root / str(self.task.get("id", "task"))
        if ws.exists():
            shutil.rmtree(ws)
        ws.mkdir(parents=True, exist_ok=True)
        raw_files = self.task.get("files", {})
        files: dict[str, str] = (
            {str(k): str(v) for k, v in raw_files.items()} if isinstance(raw_files, Mapping) else {}
        )
        for rel, content in files.items():
            self._write(ws / rel, content)
        self._restore_test(ws)
        self._state = EnvState(task=str(self.task.get("prompt", "")), workspace=ws, files=files)
        return self._state

    def step(self, edits: Mapping[str, str]) -> EnvState:
        """Apply a candidate solution (file -> content), grade it, and terminate the episode.

        Edits to the test file are applied but then overwritten with the pristine test before grading
        (a policy may not judge itself), and are never counted toward the diff-gate's "productive"
        check — only solution files changing earns reward.
        """
        state = self.state()
        ws = state.workspace
        test_name = self.task.get("test")
        before = dict(state.files)
        for rel, content in edits.items():
            self._write(ws / rel, content)
            if rel != test_name:  # the test is harness, not a solution file
                state.files[rel] = content
        self._restore_test(ws)  # neutralise any test tampering before grading
        productive = state.files != before  # diff-gate: a hollow no-op earns nothing
        verify = self._verifier or self._default_verifier
        verified = bool(productive and verify(ws))
        state.done = True
        state.reward = 1.0 if verified else 0.0
        state.info = {"productive": productive, "verified": verified}
        return state

    def state(self) -> EnvState:
        if self._state is None:
            raise RuntimeError("call reset() before step()/state()")
        return self._state

    # -- helpers ----------------------------------------------------------------
    def _restore_test(self, ws: Path) -> None:
        test_name = self.task.get("test")
        test_src = self.task.get("test_src")
        if test_name and test_src is not None:
            self._write(ws / str(test_name), str(test_src))

    def _default_verifier(self, ws: Path) -> bool:
        """Run the task's ``verify`` command (or pytest on its test) and return pass/fail."""
        from chimera.core.verify import CommandVerifier

        test = self.task.get("test")
        command = str(self.task.get("verify") or f'"{sys.executable}" -m pytest -q {test}')
        result = CommandVerifier(command, ws, timeout=self.timeout).verify()
        # `abstained` means no verification actually ran — never positive evidence, so no reward.
        return result.passed and not result.abstained

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


__all__ = ["EnvState", "TaskEnv", "Verifier"]
