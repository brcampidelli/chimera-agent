"""Study 24, M6 fork: at the trip, the workspace is copied before the run escalates.

The breaker's own ending asks for an answer with no tools, so the workspace at the trip is what
stopping would have left. `bench/tool_loop_fork` grades that copy and the escalated workspace with the
same oracle. These tests hold the three properties the pairing depends on: the copy is the state AT
the trip (nothing the stronger model does afterwards leaks into it), it is taken once, and it is taken
only when the run escalates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.providers.gateway import ToolCall
from chimera.tools.registry import Tool, ToolRegistry

WEAK, STRONG = "openrouter/openai/gpt-oss-20b", "openrouter/deepseek/deepseek-v3.2"


class _Result:
    def __init__(self, content: str, model: str) -> None:
        self.content = content
        self.model = model
        self.prompt_tokens = 100
        self.completion_tokens = 10
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.tool_calls: list[Any] = []
        self.finish_reason = "stop"
        self.route_meta: dict[str, Any] | None = None


class _Ping(Tool):
    name = "ping"
    description = "does nothing"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        return "pong"


class _Fix(Tool):
    """What the stronger model does after the trip: a file that must NOT be in the snapshot."""

    name = "fix"
    description = "writes the fix"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, **kwargs: Any) -> str:
        (self.workspace / "after_the_trip.txt").write_text("fixed", encoding="utf-8")
        return "written"


class _Backend:
    """The weak model loops on `ping`; the strong one (if `strong_loops`, it loops too) calls `fix`
    once and answers."""

    def __init__(self, *, strong_loops: bool = False) -> None:
        self.strong_loops = strong_loops
        self.fixed = False

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        model = str(kwargs.get("model") or "")
        result = _Result("done", model)
        if not kwargs.get("tools"):
            return result
        if model == WEAK or self.strong_loops:
            result.tool_calls = [ToolCall(id="p", name="ping", arguments={})]
        elif not self.fixed:
            self.fixed = True
            result.tool_calls = [ToolCall(id="f", name="fix", arguments={})]
        return result


def _run(tmp_path: Path, *, escalate: str | None, snapshot: bool = True, strong_loops: bool = False) -> tuple[Any, Path, Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "before_the_trip.txt").write_text("state", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(_Ping())
    registry.register(_Fix(ws))
    snap = tmp_path / "snap"
    config = AgentConfig(
        model=WEAK, max_steps=30, project_root=ws, escalate_on_tool_loop=escalate,
        snapshot_on_tool_loop=snap if snapshot else None,
    )
    result = Agent(_Backend(strong_loops=strong_loops), registry, config).run("do the thing")
    return result, ws, snap


def test_the_snapshot_is_the_workspace_at_the_trip(tmp_path: Path) -> None:
    result, ws, snap = _run(tmp_path, escalate=STRONG)
    assert result.stopped_reason == "final"
    assert (snap / "before_the_trip.txt").read_text(encoding="utf-8") == "state"
    assert not (snap / "after_the_trip.txt").exists()  # the stronger model's work is not in it
    assert (ws / "after_the_trip.txt").exists()  # but it is in the escalated workspace


def test_no_escalation_means_no_snapshot(tmp_path: Path) -> None:
    result, _, snap = _run(tmp_path, escalate=None)
    assert result.stopped_reason == "tool_loop"
    assert not snap.exists()


def test_the_snapshot_is_taken_once_even_when_the_stronger_model_trips_too(tmp_path: Path) -> None:
    result, _, snap = _run(tmp_path, escalate=STRONG, strong_loops=True)
    assert result.stopped_reason == "tool_loop"
    assert sorted(p.name for p in snap.iterdir()) == ["before_the_trip.txt"]


def test_an_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "earlier.txt").write_text("keep", encoding="utf-8")
    result, _, _ = _run(tmp_path, escalate=STRONG)
    assert result.stopped_reason == "final"  # a failed copy never fails the run
    assert sorted(p.name for p in snap.iterdir()) == ["earlier.txt"]
