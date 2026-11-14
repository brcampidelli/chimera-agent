"""`chimera tui` with no terminal exits 1 in under 5 s, instead of drawing into a pipe until killed.

Measured on 2026-11-14 before the fix: ``chimera tui </dev/null`` was still running at 61 s and was
killed (exit 124). On Windows the NUL device answers ``isatty()`` with True, so the check there asks
the console API instead.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

from chimera.tui.app import _is_console

ROOT = Path(__file__).resolve().parents[1]


def test_a_null_device_or_a_buffer_is_not_a_console() -> None:
    with open(os.devnull, encoding="utf-8") as nulo:
        assert _is_console(nulo) is False
    assert _is_console(io.StringIO()) is False


def test_chimera_tui_without_a_terminal_exits_one_within_five_seconds() -> None:
    # Run from the checkout, not an installed copy: the venv's `chimera` entry point may point at a
    # different tree, and this must test the code in front of it.
    codigo = "import sys; sys.argv = ['chimera', 'tui']; from chimera.cli.main import app; app()"
    proc = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=5,
        check=False,
    )
    assert proc.returncode == 1, proc.stdout.decode(errors="replace")[-400:]
    assert b"interactive terminal" in proc.stdout
