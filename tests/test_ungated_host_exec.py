"""Two host-exec paths that behaved differently from the four beside them.

Both were found by an audit asking the same question of every caller — "does this one do what the
other four do?" — which is the only reliable way to find a guard that exists and does not cover
everything. Neither failed a test before this file, because nothing had ever asked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from chimera.sandbox.base import SandboxResult
from chimera.workflow.executors import build_executors
from chimera.workflow.models import WorkflowStep


class _Recording:
    """A sandbox that records whether it was asked to run anything."""

    isolated = False

    def __init__(self) -> None:
        self.ran: list[str] = []

    def run(self, command: str, *, timeout: int = 60, cwd: Any = None) -> SandboxResult:
        self.ran.append(command)
        return SandboxResult(exit_code=0, stdout="ok")


def _shell_step(monkeypatch: Any, *, host_exec: str, tmp_path: Path) -> tuple[Any, _Recording]:
    sandbox = _Recording()
    monkeypatch.setenv("CHIMERA_HOST_EXEC", host_exec)
    monkeypatch.setattr("chimera.sandbox.get_sandbox", lambda: sandbox)
    from chimera.config import get_settings

    get_settings.cache_clear()
    execs = build_executors(workspace=tmp_path, model="x")
    return execs["shell"], sandbox


def test_a_workflow_shell_step_honours_deny(monkeypatch: Any, tmp_path: Path) -> None:
    """The regression this file exists for.

    `shell:` was the one host-exec caller of five with no gate. A workflow is also the least
    supervised of the five — it runs unattended — so the gap sat exactly where nobody was watching.
    """
    step_fn, sandbox = _shell_step(monkeypatch, host_exec="deny", tmp_path=tmp_path)
    result = step_fn(WorkflowStep(name="s", uses="shell", with_={"command": "rm -rf /"}))
    assert result.success is False
    assert "declined" in result.output
    assert sandbox.ran == [], "the command reached the sandbox despite CHIMERA_HOST_EXEC=deny"


def test_allow_still_runs(monkeypatch: Any, tmp_path: Path) -> None:
    """A gate that blocks the allowed case is a regression, not a fix."""
    step_fn, sandbox = _shell_step(monkeypatch, host_exec="allow", tmp_path=tmp_path)
    result = step_fn(WorkflowStep(name="s", uses="shell", with_={"command": "echo hi"}))
    assert result.success is True
    assert sandbox.ran == ["echo hi"]


def test_the_local_sandbox_kills_the_whole_tree() -> None:
    """`LocalSandbox` reimplemented the POSIX half of `kill_tree` and stopped there.

    On Windows the timeout path was a bare `proc.kill()`: the shell dies, everything the shell
    started keeps running and keeps the workspace locked. This project is developed on Windows, so
    the broken half was the one that mattered here.

    Asserted structurally rather than by spawning a real process tree: a test that launches
    grandchildren and hunts for orphans is slow, platform-forked and flaky, and what regressed was
    which function gets called.
    """
    import inspect

    from chimera.sandbox import local

    source = inspect.getsource(local)
    assert "kill_tree(proc)" in source, "the timeout path no longer delegates to kill_tree"
    assert "os.killpg" not in source, (
        "the POSIX-only copy is back — that is the shape of the bug: correct on Linux, silently "
        "orphaning on Windows"
    )


def test_kill_tree_covers_both_platforms() -> None:
    """The reason delegating is worth anything: the shared helper handles what the copy did not."""
    import inspect

    from chimera.proc import stdio

    source = inspect.getsource(stdio.kill_tree)
    assert "killpg" in source and "taskkill" in source


def test_kill_tree_really_kills_a_child() -> None:
    """One live check, so the structural assertions above are not the only evidence.

    ⚠️ `**_spawn_flags()` is load-bearing, not decoration. The first version of this test spawned a
    plain child, which on POSIX stays in *pytest's* process group — and `kill_tree` killpg's the
    group, so the test killed the runner. Exit code 9, no output, no failing test to read. The
    precondition is now written on `kill_tree` itself; this line is what proves the docstring.
    """
    from chimera.proc.stdio import _spawn_flags, kill_tree

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **_spawn_flags(),
    )
    kill_tree(proc)
    assert proc.wait(timeout=10) is not None
    assert proc.poll() is not None
