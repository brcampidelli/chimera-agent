"""Tests for the Context Explorer subagent (isolated repo exploration)."""

from __future__ import annotations

from pathlib import Path

from chimera.core.explorer import (
    ContextExplorer,
    ExploreRepositoryTool,
    parse_evidence,
    read_only_registry,
)
from chimera.providers.gateway import CompletionResult, MessageLike


class FakeBackend:
    """Returns a fixed completion and records how it was called (no tools -> agent stops)."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def complete(self, messages: list[MessageLike], **kwargs: object) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content=self.content, model="fake")


_BLOCK = """Here is what I found:
<final_answer>
src/auth.py:12-40 (login handler)
src/routes.py:88 (route registration)
- tests/test_auth.py (existing coverage)
</final_answer>
trailing chatter that must be ignored"""


def test_parse_evidence_reads_the_block_only() -> None:
    ev = parse_evidence(_BLOCK)
    assert [(e.path, e.lines) for e in ev] == [
        ("src/auth.py", "12-40"),
        ("src/routes.py", "88"),
        ("tests/test_auth.py", ""),
    ]
    assert ev[0].note == "login handler"


def test_parse_evidence_rejects_prose_lines() -> None:
    ev = parse_evidence("<final_answer>\nI think the login is important\nsrc/x.py:1\n</final_answer>")
    assert [e.path for e in ev] == ["src/x.py"]  # the prose sentence is dropped


def test_parse_evidence_falls_back_without_block() -> None:
    assert [e.path for e in parse_evidence("app/main.py:5-9 (entrypoint)")] == ["app/main.py"]


def test_explorer_returns_only_evidence(tmp_path: Path) -> None:
    backend = FakeBackend(_BLOCK)
    result = ContextExplorer(backend, tmp_path).explore("where is login handled?")
    assert len(result.evidence) == 3
    ctx = result.as_context()
    assert "src/auth.py:12-40" in ctx
    # isolation: the explorer's chatter never reaches the caller's context
    assert "trailing chatter" not in ctx
    assert "Here is what I found" not in ctx


def test_explore_tool_returns_compact_context(tmp_path: Path) -> None:
    tool = ExploreRepositoryTool(FakeBackend(_BLOCK), tmp_path)
    out = tool.run(query="login flow")
    assert out.startswith("Relevant code locations")
    assert "src/routes.py:88" in out
    assert tool.run(query="") == "error: query is required"


def test_read_only_registry_has_no_write_tool(tmp_path: Path) -> None:
    reg = read_only_registry(tmp_path)
    names = set(reg.names())
    assert names == {"read_file", "list_dir", "grep", "glob"}
    assert "write_file" not in names  # the explorer cannot mutate the repo
