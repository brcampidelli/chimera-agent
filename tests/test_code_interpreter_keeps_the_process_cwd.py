"""The in-process code interpreter never moves the backend's working directory.

The backend resolves its `.env` (the keys, and the file the own-files fence protects) against its
working directory. An `os.chdir` inside `code_interpreter` used to outlive the call, and an
installed backend was found standing in a project folder — guarding a `.env` that does not exist
there, and refusing the project through the bridge as "the app's own data".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chimera.tools.code import CodeInterpreterTool


def test_a_chdir_inside_the_code_does_not_outlive_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "install"
    elsewhere = tmp_path / "project"
    home.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(home)
    out = CodeInterpreterTool().run(
        code=f"import os\nos.chdir({str(elsewhere)!r})\nprint(os.getcwd())"
    )
    assert str(elsewhere) in out  # the program did move, while it ran
    assert Path(os.getcwd()) == home  # and the process is back where it was


def test_the_cwd_is_restored_when_the_code_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    out = CodeInterpreterTool().run(code="import os\nos.chdir('sub')\nraise ValueError('boom')")
    assert "ValueError: boom" in out
    assert Path(os.getcwd()) == tmp_path


def test_state_still_persists_across_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    tool = CodeInterpreterTool()
    tool.run(code="x = 41")
    assert tool.run(code="print(x + 1)") == "42"
