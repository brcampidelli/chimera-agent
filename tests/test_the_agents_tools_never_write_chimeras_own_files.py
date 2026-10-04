"""The agent's write tools never write Chimera's own `.env` or anything in its data folder.

Review of 2026-10-04 (LOW). The workspace tools refused a path outside the project and the desktop
shell's two files (`SHELL_OWNED_ENV`), and nothing else of Chimera's: a turn whose workspace
contains the install folder — an app started from the home directory, a test's temporary folder —
could `write_file` the `.env` that holds the posture and the keys, or drop an answer into
`<home>/approvals`. Now every write verb refuses both, for every turn and every posture, inside the
workspace or outside it with a person's yes. Another project's `.env` is ordinary work and is not
touched: only Chimera's own, by identity.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.tools.edit import EditFileTool
from chimera.tools.files import WriteFileTool
from chimera.tools.workspace import PathEscapesWorkspaceError, ProtectedPathError, resolve_for


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)  # Chimera's own `.env` is the one in the working directory
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    (tmp_path / ".env").write_text("CHIMERA_REACH=read_only\n", encoding="utf-8")
    (tmp_path / "home" / "approvals").mkdir(parents=True)
    return tmp_path


def _refused(call: Any) -> bool:
    try:
        out = call()
    except PathEscapesWorkspaceError:
        return True
    return isinstance(out, str) and out.startswith("error")


@pytest.mark.parametrize(
    "path",
    [".env", "home/approvals/q1.answer.json", "home/memory.json", "./sub/../.env"],
)
def test_write_and_edit_refuse_chimeras_own_files_inside_the_workspace(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _setup(tmp_path, monkeypatch)
    write = WriteFileTool(ws)
    assert _refused(lambda: write.run(path=path, content="CHIMERA_REACH=workspace_shell\n"))
    if path.endswith(".env"):
        edit = EditFileTool(ws)
        assert _refused(
            lambda: edit.run(path=path, old="read_only", new="workspace_shell")
        )
    assert (ws / ".env").read_text(encoding="utf-8") == "CHIMERA_REACH=read_only\n"
    assert not (ws / "home" / "approvals" / "q1.answer.json").exists()
    get_settings.cache_clear()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spellings")
@pytest.mark.parametrize("path", [".env ", ".env.", ".env::$DATA", ".ENV"])
def test_no_spelling_of_the_env_gets_through(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _setup(tmp_path, monkeypatch)
    with pytest.raises(ProtectedPathError):
        resolve_for(WriteFileTool(ws), path, verb="write")
    get_settings.cache_clear()


def test_a_yes_at_the_screen_does_not_grant_them_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _setup(tmp_path, monkeypatch)
    project = ws / "proj"
    project.mkdir()
    tool = WriteFileTool(project)
    tool.ask_outside = lambda _q: True  # type: ignore[attr-defined]
    with pytest.raises(ProtectedPathError):
        resolve_for(tool, str(ws / ".env"), verb="write")
    with pytest.raises(ProtectedPathError):
        resolve_for(tool, str(ws / "home" / "approvals" / "x.answer.json"), verb="write")
    get_settings.cache_clear()


def test_another_projects_env_file_is_ordinary_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _setup(tmp_path, monkeypatch)
    project = ws / "web"
    project.mkdir()
    out = WriteFileTool(project).run(path=".env.local", content="NEXT_PUBLIC_API=x\n")
    assert not out.startswith("error"), out
    assert (project / ".env.local").read_text(encoding="utf-8") == "NEXT_PUBLIC_API=x\n"
    # (A project's plain `.env` keeps the answer it already had from the write region, which
    # refuses every `.env` by name; this guard adds nothing to it and takes nothing away.)
    get_settings.cache_clear()
