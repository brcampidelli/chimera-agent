"""Tests for the streaming command-runner behind the Code screen's command-runner panel.

Uses ``python -c`` (mirrors tests/test_sandbox.py) so it's portable across the CI shells and doesn't
depend on a specific shell beyond a ``python`` on PATH.
"""

from __future__ import annotations

from pathlib import Path

from chimera.api.exec_stream import resolve_exec_cwd, run_streamed


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


def test_timeout_kills_and_reports(tmp_path: Path) -> None:
    lines, code = _collect('python -c "import time; time.sleep(30)"', tmp_path, timeout=1)
    assert code != 0  # killed process reports a non-zero exit
    assert any("timed out" in line for line in lines)
