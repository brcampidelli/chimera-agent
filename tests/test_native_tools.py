"""Tests for the native file/shell/http tools."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.tools import (
    HttpGetTool,
    ListDirTool,
    PathEscapesWorkspaceError,
    ReadFileTool,
    RunShellTool,
    WriteFileTool,
    default_registry,
    resolve_in_workspace,
)


def test_write_then_read(tmp_path: Path) -> None:
    WriteFileTool(tmp_path).run(path="sub/a.txt", content="hello")
    assert (tmp_path / "sub" / "a.txt").read_text(encoding="utf-8") == "hello"
    assert ReadFileTool(tmp_path).run(path="sub/a.txt") == "hello"


def test_read_missing_file(tmp_path: Path) -> None:
    out = ReadFileTool(tmp_path).run(path="nope.txt")
    assert "not found" in out


def test_list_dir(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("1", encoding="utf-8")
    (tmp_path / "d").mkdir()
    out = ListDirTool(tmp_path).run(path=".")
    assert "x.txt" in out
    assert "d/" in out


def test_path_escape_is_blocked(tmp_path: Path) -> None:
    with pytest.raises(PathEscapesWorkspaceError):
        resolve_in_workspace(tmp_path, "../../etc/passwd")
    with pytest.raises(PathEscapesWorkspaceError):
        ReadFileTool(tmp_path).run(path="../outside.txt")


def test_run_shell_echo(tmp_path: Path) -> None:
    out = RunShellTool(tmp_path).run(command="echo hi")
    assert "hi" in out
    assert "[exit 0]" in out


def test_http_get_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def fake_get(url: str, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(status_code=200, text="page-body")

    monkeypatch.setattr(httpx, "get", fake_get)
    out = HttpGetTool().run(url="https://example.com")
    assert "[200]" in out
    assert "page-body" in out


def test_default_registry_includes_native_tools(tmp_path: Path) -> None:
    registry = default_registry(tmp_path)
    for name in ("echo", "read_file", "write_file", "list_dir", "run_shell", "http_get"):
        assert name in registry
