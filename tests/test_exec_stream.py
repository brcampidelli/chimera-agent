"""Tests for the streaming command-runner behind the Code screen's command-runner panel.

Uses ``python -c`` (mirrors tests/test_sandbox.py) so it's portable across the CI shells and doesn't
depend on a specific shell beyond a ``python`` on PATH.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest

from chimera.api.exec_stream import resolve_exec_cwd, run_streamed
from chimera.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _host_sandbox(monkeypatch: pytest.MonkeyPatch):
    """These tests measure live host streaming, not isolated one-shot execution."""
    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    monkeypatch.delenv("CHIMERA_HOST_EXEC", raising=False)
    monkeypatch.delenv("CHIMERA_REACH", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _collect(command: str, workspace: Path, **kwargs: object) -> tuple[list[str], int]:
    lines: list[str] = []
    code = run_streamed(command, workspace=workspace, on_line=lines.append, **kwargs)  # type: ignore[arg-type]
    return lines, code


def test_streams_lines_and_exit_zero(tmp_path: Path) -> None:
    lines, code = _collect('python -c "print(1); print(2)"', tmp_path)
    assert code == 0
    assert "1" in lines and "2" in lines


def test_failing_command_returns_nonzero(tmp_path: Path) -> None:
    _, code = _collect('python -c "import sys; sys.exit(3)"', tmp_path)
    assert code != 0


def test_merges_stderr_into_the_stream(tmp_path: Path) -> None:
    # A command-runner shows combined output — stderr is merged into the streamed lines.
    lines, code = _collect('python -c "import sys; sys.stderr.write(\'boom\\n\')"', tmp_path)
    assert code == 0
    assert any("boom" in line for line in lines)


def test_cwd_escape_is_rejected(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        run_streamed("python -c \"pass\"", workspace=tmp_path, cwd="../..", on_line=lambda _s: None)
    with pytest.raises(ValueError):
        resolve_exec_cwd(tmp_path, "../..")


@pytest.fixture
def os_sandbox_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from chimera.sandbox.os_sandbox import os_sandbox_available

    if not os_sandbox_available():
        pytest.skip("OS sandbox (bubblewrap or Seatbelt) is unavailable")
    monkeypatch.setenv("CHIMERA_SANDBOX", "os")
    get_settings.cache_clear()
    yield Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_SANDBOX="os")
    get_settings.cache_clear()


def test_os_sandbox_streams_output_by_lines(
    tmp_path: Path, os_sandbox_settings: Settings
) -> None:
    observed = threading.Event()
    lines: list[str] = []

    def on_line(line: str) -> None:
        lines.append(line)
        if line == "first":
            observed.set()

    def run() -> None:
        run_streamed(
            f'"{sys.executable}" -c "import time; print(\'first\', flush=True); time.sleep(1); print(\'second\', flush=True)"',
            workspace=tmp_path,
            timeout=10,
            on_line=on_line,
            settings=os_sandbox_settings,
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert observed.wait(20), "the first sandboxed line did not arrive live"
    assert "second" not in lines
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert "second" in lines


def test_timeout_kills_and_reports(tmp_path: Path) -> None:
    lines, code = _collect('python -c "import time; time.sleep(30)"', tmp_path, timeout=1)
    assert code != 0  # killed process reports a non-zero exit
    assert any("timed out" in line for line in lines)


def _run_with_orphan(tmp_path: Path) -> tuple[bool, list[str], int | None]:
    """A `npm run dev`-style command: the driver starts a grandchild that inherits its stdout, writes
    its own pid, and sleeps 120 s, then the driver exits. Returns (the run was still going after 60 s,
    the lines it streamed, the grandchild's pid). The thread keeps a regression a failed assertion,
    not a stuck test run."""
    (tmp_path / "grandchild.py").write_text(
        "import os, time\n"
        "with open('grandchild.pid', 'w') as f:\n"
        "    f.write(str(os.getpid()))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    (tmp_path / "driver.py").write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, 'grandchild.py'])\n"
        "print('started', flush=True)\n",
        encoding="utf-8",
    )
    lines: list[str] = []
    thread = threading.Thread(
        target=run_streamed,
        args=(f'"{sys.executable}" driver.py',),
        kwargs={"workspace": tmp_path, "timeout": 60, "on_line": lines.append},
        daemon=True,
    )
    thread.start()
    thread.join(timeout=60)
    pidfile = tmp_path / "grandchild.pid"
    pid = int(pidfile.read_text(encoding="utf-8")) if pidfile.exists() else None
    return thread.is_alive(), lines, pid


def _reap(pid: int | None) -> None:
    """Kill a grandchild the test left behind, so a failing run does not leak a 120 s sleeper."""
    if pid is None:
        return
    with suppress(Exception):
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGKILL)


def test_a_child_that_outlives_its_command_does_not_hold_the_run_open(tmp_path: Path) -> None:
    # The command is finished when it exits, so the run must end then. Before this, the run waited
    # for the pipe to close, which the grandchild holds for as long as it lives — on Windows, where a
    # timeout's tree-kill cannot reach a process whose parent has already exited, that was forever.
    still_running, lines, pid = _run_with_orphan(tmp_path)
    try:
        assert not still_running, "the run waited for a grandchild that outlived its command"
        assert "started" in lines
    finally:
        _reap(pid)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups: the run kills the orphan's group")
def test_on_posix_the_orphan_is_dead_when_the_run_returns(tmp_path: Path) -> None:
    # A run that ends with a live orphan in its group would leave the orphan unreachable by Stop or the
    # timeout, since the direct child is gone. The run's grace-end kill must reach it first.
    still_running, _lines, pid = _run_with_orphan(tmp_path)
    try:
        assert not still_running
        assert pid is not None, "the grandchild never wrote its pid"
        deadline = time.monotonic() + 5
        while _posix_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _posix_alive(pid), "the orphan outlived the run"
    finally:
        _reap(pid)


def _posix_alive(pid: int) -> bool:
    """Alive means not reaped and not a zombie. A zombie answers kill(0) until its parent reaps it."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text(encoding="utf-8").split()[2] != "Z"
    except OSError:
        return True
