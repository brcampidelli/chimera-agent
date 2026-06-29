"""Run tools through the trust kernel.

``GovernedTool`` wraps any :class:`~chimera.tools.base.Tool` so its ``run`` is gated
by the kernel: BLOCK refuses, REVIEW requires approval, WARN/ALLOW proceed. Because
it *is* a Tool, a registry of governed tools drops straight into the existing agent
loop with no other changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chimera.governance.kernel import TrustKernel
from chimera.governance.policy import Decision, Verdict
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

ApproveFn = Callable[[Verdict, str], bool]


class GovernedTool(Tool):
    """A tool whose execution is gated by the trust kernel."""

    def __init__(self, inner: Tool, kernel: TrustKernel, *, approve: ApproveFn | None = None) -> None:
        self.inner = inner
        self.kernel = kernel
        self.approve = approve
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters

    def run(self, **kwargs: Any) -> str:
        action = f"{self.name} {kwargs}"
        verdict = self.kernel.evaluate(action)
        if verdict.decision == Decision.BLOCK:
            return f"[governance: BLOCKED — {verdict.reason}]"
        if verdict.decision == Decision.REVIEW:
            approved = self.approve(verdict, action) if self.approve else False
            if not approved:
                return f"[governance: needs review — {verdict.reason}]"
        return self.inner.run(**kwargs)


def govern_registry(
    registry: ToolRegistry, kernel: TrustKernel, *, approve: ApproveFn | None = None
) -> ToolRegistry:
    """Return a new registry with every tool wrapped in a :class:`GovernedTool`."""
    governed = ToolRegistry()
    for tool in registry.tools():
        governed.register(GovernedTool(tool, kernel, approve=approve))
    return governed
