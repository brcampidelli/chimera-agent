"""Tests for the generic SubAgentTool (spawn an isolated, tool-scoped subagent)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core import SubAgentTool
from chimera.providers import CompletionResult
from chimera.tools import EchoTool, ReadFileTool, ToolRegistry, WriteFileTool


class FakeBackend:
    """Returns a fixed final answer (no tool calls -> the subagent loop stops immediately)."""

    def __init__(self, answer: str = "done") -> None:
        self.answer = answer
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content=self.answer, model="fake")


def _registry(tmp_path: Path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.register(ReadFileTool(tmp_path))
    reg.register(WriteFileTool(tmp_path))
    return reg


def test_build_registry_respects_allowlist(tmp_path: Path) -> None:
    tool = SubAgentTool(FakeBackend(), _registry(tmp_path), allowed=["echo", "read_file"])
    # a requested tool outside the allowlist is dropped
    assert set(tool._build_registry(["echo", "write_file"]).names()) == {"echo"}
    # no request -> the whole allowlist
    assert set(tool._build_registry(None).names()) == {"echo", "read_file"}


def test_no_recursion_spawn_is_never_granted(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    tool = SubAgentTool(FakeBackend(), reg)
    reg.register(tool)  # the registry now contains spawn_subagent too
    assert "spawn_subagent" not in tool._allowed
    assert "spawn_subagent" not in tool._build_registry(["spawn_subagent", "echo"]).names()


def test_run_returns_only_the_answer(tmp_path: Path) -> None:
    tool = SubAgentTool(FakeBackend("SUBRESULT"), _registry(tmp_path))
    assert tool.run(task="do a self-contained thing") == "SUBRESULT"


def test_run_requires_a_task(tmp_path: Path) -> None:
    assert SubAgentTool(FakeBackend(), _registry(tmp_path)).run(task="   ") == "error: task is required"
