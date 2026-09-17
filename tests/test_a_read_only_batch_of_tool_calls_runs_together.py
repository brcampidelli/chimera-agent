"""A step that asks for several read-only tools gets them at the same time; anything else, in turn.

Item 6(a) of the list audited on 2026-09-16. The loop was written one call at a time and never
revisited when providers started returning several calls per step. Measured on the desktop's own
traces before this existed (1,221 steps): 205 carried more than one call, 143 of those read-only
throughout — 12% of all steps. The gain is bounded by the slowest call, so it is real for fetches
and nothing for local reads; the step record says how many ran together, so a trace can tell which.

The measurement here is deterministic: four fake tools that each take a fifth of a second. In turn
they cost at least 0.8 s; together, well under half of that. The safety half is pinned harder than
the speed half: a batch with one write in it runs in turn, an unlisted tool keeps the old loop, the
transcript stays in the model's order whatever finished first, and a batch that ran together never
reports a call as "not run".
"""

from __future__ import annotations

import threading
import time
from typing import Any

from chimera.core import Agent, AgentConfig
from chimera.core.agent import PARALLEL_READ_TOOLS, PARALLEL_READ_WORKERS
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.base import Tool


class _Slow(Tool):
    """A read-only tool that takes `delay` and records when it ran, on which thread."""

    description = "slow"
    parameters: dict[str, Any] = {"type": "object", "properties": {"x": {"type": "string"}}}
    log: list[tuple[str, float, float, int]] = []

    def __init__(self, name: str, delay: float = 0.2) -> None:
        self.name = name
        self.delay = delay

    def run(self, **kwargs: Any) -> str:
        began = time.monotonic()
        time.sleep(self.delay)
        _Slow.log.append((self.name, began, time.monotonic(), threading.get_ident()))
        return f"{self.name}:{kwargs.get('x', '')}"


class _Backend:
    def __init__(self, calls: list[ToolCall]) -> None:
        self._calls = [calls]
        self.messages: list[Any] = []

    def complete(self, messages: list[Any], *, tools: Any = None, **_: Any) -> CompletionResult:
        self.messages = list(messages)
        if self._calls:
            return CompletionResult(content="", model="fake", tool_calls=self._calls.pop(0))
        return CompletionResult(content="done", model="fake")


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_Slow(name))
    return registry


def _run(
    names: list[str], registered: tuple[str, ...] | None = None
) -> tuple[Any, _Backend, float]:
    _Slow.log.clear()
    calls = [
        ToolCall(id=f"c{i}", name=name, arguments={"x": str(i)}) for i, name in enumerate(names)
    ]
    backend = _Backend(calls)
    agent = Agent(
        backend, _registry(*(registered or tuple(dict.fromkeys(names)))), AgentConfig(max_steps=3)
    )
    began = time.monotonic()
    result = agent.run("go")
    return result, backend, time.monotonic() - began


# ------------------------------------------------------------------ the speed half, measured


def test_four_read_only_calls_run_together_and_the_step_says_so() -> None:
    result, backend, elapsed = _run(["read_file", "read_file", "http_get", "grep"])

    assert elapsed < 0.5, f"in turn this batch costs 0.8 s; together it took {elapsed:.2f} s"
    assert len({t for _, _, _, t in _Slow.log}) > 1, "ran on one thread — it did not run together"
    assert result.steplog.steps[0].ran_together == 4
    assert result.steplog.steps[0].as_dict()["ran_together"] == 4


def test_a_batch_with_one_write_in_it_runs_in_turn(monkeypatch: Any) -> None:
    result, backend, elapsed = _run(["read_file", "write_file", "read_file"])

    assert elapsed >= 0.6, f"a write was in the batch and it still ran together ({elapsed:.2f} s)"
    assert len({t for _, _, _, t in _Slow.log}) == 1
    assert result.steplog.steps[0].ran_together == 0


def test_an_unlisted_tool_keeps_the_old_loop() -> None:
    """An MCP tool, a `browser` — semantics unknown or stateful. Not on the list, not together."""
    assert "loja_execute_query" not in PARALLEL_READ_TOOLS and "browser" not in PARALLEL_READ_TOOLS
    result, _, elapsed = _run(["read_file", "loja_execute_query"])

    assert elapsed >= 0.4
    assert result.steplog.steps[0].ran_together == 0


def test_a_single_call_is_not_a_batch() -> None:
    result, _, _ = _run(["http_get"])
    assert result.steplog.steps[0].ran_together == 0


# ------------------------------------------------------------------ the safety half


def test_observations_come_back_in_the_models_order_whatever_finished_first() -> None:
    _Slow.log.clear()
    registry = ToolRegistry()
    registry.register(_Slow("read_file", delay=0.3))
    registry.register(_Slow("grep", delay=0.05))
    calls = [
        ToolCall(id="first", name="read_file", arguments={"x": "slow"}),
        ToolCall(id="second", name="grep", arguments={"x": "fast"}),
    ]
    backend = _Backend(calls)
    Agent(backend, registry, AgentConfig(max_steps=3)).run("go")

    tool_messages = [m for m in backend.messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["first", "second"]
    assert (
        tool_messages[0]["content"] == "read_file:slow"
        and tool_messages[1]["content"] == "grep:fast"
    )
    finished_first = min(_Slow.log, key=lambda e: e[2])[0]
    assert finished_first == "grep", "the fast one finished first and still came second"


def test_the_batch_is_bounded_to_four_at_once() -> None:
    result, _, elapsed = _run(["read_file"] * 8)

    assert result.steplog.steps[0].ran_together == 8
    # Eight fifths of a second in turn; two rounds of four together.
    assert 0.35 < elapsed < 1.2, (
        f"{elapsed:.2f} s — neither bounded (>= 1.6 s in turn) nor together"
    )
    assert PARALLEL_READ_WORKERS == 4


def test_a_failing_call_in_the_batch_is_an_error_observation_for_that_call_only() -> None:
    class _Boom(Tool):
        name = "list_dir"
        description = "boom"
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        def run(self, **_: Any) -> str:
            raise RuntimeError("no such directory")

    registry = ToolRegistry()
    registry.register(_Slow("read_file", delay=0.05))
    registry.register(_Boom())
    calls = [
        ToolCall(id="a", name="read_file", arguments={"x": "ok"}),
        ToolCall(id="b", name="list_dir", arguments={}),
    ]
    backend = _Backend(calls)
    Agent(backend, registry, AgentConfig(max_steps=3)).run("go")

    tool_messages = {
        m["tool_call_id"]: m["content"] for m in backend.messages if m.get("role") == "tool"
    }
    assert tool_messages["a"] == "read_file:ok"
    assert tool_messages["b"].startswith("error: tool 'list_dir' failed: no such directory")


# ------------------------------------------------------------------ through the governed chain the desktop builds


def test_a_governed_read_only_batch_keeps_the_audit_chain_whole(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The desktop's tools are wrapped twice — kernel (in `observe` since 0.59.0) and taint ledger —
    and both write shared state: the audit log under its lock, the ledger's event index under the
    lock this change added. Four reads together must leave a chain that verifies and a ledger with
    four distinct events."""
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings
    from chimera.governance.audit import AuditLog
    from chimera.providers.gateway import LLMGateway

    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(4):
        (ws / f"f{i}.txt").write_text(f"file {i}", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_GOVERNANCE", "observe")
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_GOVERNANCE="observe")  # type: ignore[call-arg]
    registry, ledger = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn"
    )
    calls = [
        ToolCall(id=f"c{i}", name="read_file", arguments={"path": f"f{i}.txt"}) for i in range(4)
    ]
    backend = _Backend(calls)

    result = Agent(backend, registry, AgentConfig(max_steps=3)).run("go")

    tool_messages = {
        m["tool_call_id"]: m["content"] for m in backend.messages if m.get("role") == "tool"
    }
    assert tool_messages == {f"c{i}": f"file {i}" for i in range(4)}
    assert result.steplog.steps[0].ran_together == 4
    assert AuditLog(settings.home / "audit.jsonl").verify().ok, (
        "concurrent audit writes broke the chain"
    )
    assert len({e.seq for e in ledger.events}) == len(ledger.events), "two events took one seq"
