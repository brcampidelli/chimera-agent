"""Tests for the native file/shell/http tools."""

from __future__ import annotations

from pathlib import Path

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


class _RecordingSandbox:
    """Captures the (timeout, cwd) a shell call resolves to, without running anything."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None):  # type: ignore[no-untyped-def]
        from chimera.sandbox.base import SandboxResult

        self.calls.append({"command": command, "timeout": timeout, "cwd": cwd})
        return SandboxResult(exit_code=0, stdout="ok")


def test_run_shell_cwd_runs_in_subdir(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    sandbox = _RecordingSandbox()
    RunShellTool(tmp_path, sandbox=sandbox).run(command="ls", cwd="sub")
    assert sandbox.calls[0]["cwd"] == (tmp_path / "sub").resolve()


def test_run_shell_cwd_escape_is_blocked(tmp_path: Path) -> None:
    out = RunShellTool(tmp_path).run(command="echo hi", cwd="../..")
    assert out.startswith("error:")
    assert "escapes the workspace" in out


def test_run_shell_timeout_is_capped(tmp_path: Path) -> None:
    sandbox = _RecordingSandbox()
    RunShellTool(tmp_path, sandbox=sandbox, max_timeout=120).run(command="sleep 1", timeout=99999)
    assert sandbox.calls[0]["timeout"] == 120  # clamped to the configured ceiling


class _FakeResponse:
    """A minimal httpx streaming-response stand-in for the http_get manual-redirect loop."""

    def __init__(self, *, status: int = 200, body: bytes = b"page-body", location: str | None = None) -> None:
        self.status_code = status
        self._body = body
        self.encoding = "utf-8"
        self.is_redirect = location is not None
        self.headers = {"location": location} if location else {}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def iter_bytes(self):  # type: ignore[no-untyped-def]
        yield self._body


class _FakeClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def stream(self, method: str, url: str) -> _FakeResponse:
        return _FakeResponse()


def _offline_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Map any hostname to a public IP so the SSRF guard passes without real DNS."""
    import chimera.scrape.ssrf as ssrf

    monkeypatch.setattr(ssrf, "_resolve_ips", lambda host: ["93.184.216.34"])


def test_http_get_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    _offline_ssrf(monkeypatch)
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    out = HttpGetTool().run(url="https://example.com")
    assert "[200]" in out
    assert "page-body" in out


def test_http_get_blocks_ssrf_metadata_endpoint() -> None:
    # A private/metadata target must be refused before any request is made.
    out = HttpGetTool().run(url="http://169.254.169.254/latest/meta-data/")
    assert out.startswith("error:") and "blocked" in out


def test_http_get_blocks_non_http_scheme() -> None:
    assert HttpGetTool().run(url="file:///etc/passwd").startswith("error:")


def test_default_registry_includes_native_tools(tmp_path: Path) -> None:
    registry = default_registry(tmp_path)
    for name in ("echo", "read_file", "write_file", "list_dir", "run_shell", "http_get"):
        assert name in registry


def test_write_file_is_byte_exact_no_newline_translation(tmp_path: Path) -> None:
    from chimera.tools import WriteFileTool

    WriteFileTool(tmp_path).run(path="a.txt", content="line1\nline2\n")
    assert (tmp_path / "a.txt").read_bytes() == b"line1\nline2\n"  # '\n' kept, not OS-translated
    assert not (tmp_path / "a.txt.chimera-tmp").exists()
