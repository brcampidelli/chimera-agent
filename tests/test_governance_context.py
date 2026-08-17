"""`TrustKernel.evaluate` accepted a `context` and threw it away.

The signature declared `context: str = ""`; the body never mentioned it; the one production caller
(`GovernedTool`) never passed it. A parameter that silently discards its argument is worse than no
parameter at all, because a caller can believe the kernel is judging with information it never got.

These tests pin both ends of the wire that now exists: the judge receives it when the judge can take
it, the audit records it always, and neither breaks a judge written before any of this.
"""

from __future__ import annotations

from typing import Any

from chimera.governance.audit import AuditLog
from chimera.governance.governed_tool import GovernedTool, govern_registry
from chimera.governance.kernel import TrustKernel, _accepts_context
from chimera.governance.policy import Decision, Verdict
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry


class _Echo(Tool):
    name = "echo"
    description = "echo"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        return "ran"


def test_a_two_argument_judge_receives_the_context() -> None:
    seen: list[tuple[str, str]] = []

    def judge(action: str, context: str) -> Verdict:
        seen.append((action, context))
        return Verdict(Decision.ALLOW, "fine", "judge")

    TrustKernel(judge=judge).evaluate("rm -rf build/", context="task: rebuild the wheel")
    assert seen == [("rm -rf build/", "task: rebuild the wheel")]


def test_a_one_argument_judge_still_works() -> None:
    """Every judge written before this took one argument. Widening must not break them."""
    calls: list[str] = []

    def judge(action: str) -> Verdict:
        calls.append(action)
        return Verdict(Decision.ALLOW, "fine", "judge")

    TrustKernel(judge=judge).evaluate("ls", context="ignored, and that is correct")
    assert calls == ["ls"]


def test_an_unreadable_signature_falls_back_to_one_argument() -> None:
    """Fails closed to the old shape: a judge whose arity cannot be read keeps working."""

    class Callable_:
        def __call__(self, *args: Any) -> Verdict:
            return Verdict(Decision.ALLOW, "fine", "judge")

    assert _accepts_context(Callable_()) is False
    assert _accepts_context(None) is False
    assert _accepts_context(lambda a, c: None) is True


def test_the_audit_records_the_context(tmp_path: Any) -> None:
    """The cheaper half of the wire, and the one that matters when somebody reads the log later
    asking why an action was allowed. `record` flattens the payload into the entry itself."""
    log = AuditLog(tmp_path / "audit.jsonl")
    TrustKernel(audit=log).evaluate("touch x", context="task: create the fixture")
    assert log.entries()[-1]["context"] == "task: create the fixture"


def test_no_context_leaves_no_empty_column(tmp_path: Any) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    TrustKernel(audit=log).evaluate("touch x")
    assert "context" not in log.entries()[-1]


def test_the_context_cannot_forge_the_hash_chain(tmp_path: Any) -> None:
    """A new payload key must not become a way to overwrite `prev`/`hash`.

    `record` writes the chain fields last for exactly this reason; adding a field is the moment to
    prove that still holds, since the audit trail's whole value is being tamper-evident.
    """
    log = AuditLog(tmp_path / "audit.jsonl")
    TrustKernel(audit=log).evaluate("a", context="one")
    TrustKernel(audit=log).evaluate("b", context="two")
    assert log.verify().ok


def test_governed_tool_passes_its_context_through() -> None:
    """The wiring test. Without it, the kernel could take context that no caller ever sends."""
    seen: list[str] = []

    def judge(action: str, context: str) -> Verdict:
        seen.append(context)
        return Verdict(Decision.ALLOW, "fine", "judge")

    tool = GovernedTool(_Echo(), TrustKernel(judge=judge), context=lambda: "task: ship the release")
    assert tool.run() == "ran"
    assert seen == ["task: ship the release"]


def test_the_context_is_read_fresh_at_every_call() -> None:
    """A fixed string would pin the first task forever and label later actions with a stale reason."""
    task = ["first"]
    seen: list[str] = []

    def judge(action: str, context: str) -> Verdict:
        seen.append(context)
        return Verdict(Decision.ALLOW, "fine", "judge")

    tool = GovernedTool(_Echo(), TrustKernel(judge=judge), context=lambda: task[0])
    tool.run()
    task[0] = "second"
    tool.run()
    assert seen == ["first", "second"]


def test_a_broken_context_provider_does_not_block_the_tool() -> None:
    """Governance fails OPEN on a missing reason: the verdict is still made, just with less signal."""

    def boom() -> str:
        raise RuntimeError("somebody's callable has a typo")

    tool = GovernedTool(_Echo(), TrustKernel(), context=boom)
    assert tool.run() == "ran"


def test_govern_registry_forwards_the_context() -> None:
    registry = ToolRegistry()
    registry.register(_Echo())
    governed = govern_registry(registry, TrustKernel(), context=lambda: "why")
    wrapped = next(iter(governed.tools()))
    assert isinstance(wrapped, GovernedTool)
    assert wrapped._context() == "why"
