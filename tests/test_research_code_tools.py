"""Tests for the execute_code / arxiv_search / youtube_transcript reference tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.sandbox.base import SandboxResult
from chimera.tools.code import ExecuteCodeTool
from chimera.tools.research import ArxivSearchTool, YouTubeTranscriptTool, _youtube_id


class FakeSandbox:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        self.commands.append(command)
        return SandboxResult(exit_code=0, stdout="hello\n")


def test_execute_code_runs_via_sandbox_and_cleans_up(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    out = ExecuteCodeTool(workspace=tmp_path, sandbox=sandbox).run(code="print('hello')")
    assert "[exit 0]" in out and "hello" in out
    assert sandbox.commands and sandbox.commands[0].startswith('python "')
    assert not list(tmp_path.glob(".chimera_exec_*.py"))  # temp script removed


def test_execute_code_real_local_run(tmp_path: Path) -> None:
    from chimera.sandbox import LocalSandbox

    out = ExecuteCodeTool(workspace=tmp_path, sandbox=LocalSandbox()).run(code="print(6 * 7)")
    assert "42" in out


_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762</id>
    <title>Attention Is All You Need</title>
    <summary>We propose the Transformer, a new architecture.</summary>
    <author><name>Ashish Vaswani</name></author>
  </entry>
</feed>"""


def test_arxiv_search_parses_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class Resp:
        text = _ATOM

        def raise_for_status(self) -> None: ...

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    out = ArxivSearchTool().run(query="transformer")
    assert "Attention Is All You Need" in out and "1706.03762" in out and "Vaswani" in out


def test_arxiv_search_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class Resp:
        text = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

        def raise_for_status(self) -> None: ...

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    assert "no arXiv results" in ArxivSearchTool().run(query="zzzznotathing")


def test_youtube_id_extraction() -> None:
    assert _youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _youtube_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _youtube_id("not a video") is None


def test_youtube_transcript_bad_id_and_missing_lib() -> None:
    assert YouTubeTranscriptTool().run(video="not a video").startswith("error:")
    # lib not installed (or transcript unavailable) -> a handled error, never a crash
    out = YouTubeTranscriptTool().run(video="dQw4w9WgXcQ")
    assert out.startswith("error:")


def test_reference_tools_registered() -> None:
    from chimera.tools import default_registry

    names = set(default_registry().names())
    assert {"execute_code", "arxiv_search", "youtube_transcript"} <= names
