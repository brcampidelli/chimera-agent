"""Tests for the streaming command-runner behind the Code screen's command-runner panel.

Uses ``python -c`` (mirrors tests/test_sandbox.py) so it's portable across the CI shells and doesn't
depend on a specific shell beyond a ``python`` on PATH.
"""

from __future__ import annotations

import sys
import threading
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
