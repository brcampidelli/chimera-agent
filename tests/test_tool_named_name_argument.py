"""A tool whose own argument is called ``name`` runs through the registry and the wrappers around it.

Live, 2026-10-27: ``skill_view`` failed on every call with "ToolRegistry.run() got multiple values for
argument 'name'". The registry's first parameter was called ``name`` (the tool's name), so the tool's
``name=`` argument collided with it before any tool ran. The rule these tests pin: the registry's tool
name is positional-only, and every wrapper that forwards arguments by ``**kwargs`` keeps ``name`` intact.
"""

from __future__ import annotations

from typing import Any

from chimera.governance.governed_tool import GovernedTool
from chimera.governance.kernel import TrustKernel
from chimera.governance.ledger import TaintLedger
from chimera.governance.ledger_tool import LedgeredTool
from chimera.skills.aliases import SkillView
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry


class _NamedTool(Tool):
    """A tool whose only argument is called ``name``, as ``skill_view``'s is."""

    name = "lookup_skill"
    description = "Look up a skill by its name."
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.seen.append(dict(kwargs))
        return f"found {kwargs['name']}"


def test_the_registry_hands_an_argument_named_name_to_the_tool() -> None:
    registry = ToolRegistry()
    tool = _NamedTool()
    registry.register(tool)

    assert registry.run("lookup_skill", name="pdf-forms") == "found pdf-forms"
    assert tool.seen == [{"name": "pdf-forms"}]


def test_a_ledger_wrapper_hands_the_name_argument_through() -> None:
    inner = _NamedTool()
    registry = ToolRegistry()
    registry.register(LedgeredTool(inner, TaintLedger()))

    assert registry.run("lookup_skill", name="pdf-forms") == "found pdf-forms"
    assert inner.seen == [{"name": "pdf-forms"}]


def test_a_governance_wrapper_hands_the_name_argument_through() -> None:
    inner = _NamedTool()
    registry = ToolRegistry()
    registry.register(GovernedTool(inner, TrustKernel(), approve=lambda _verdict, _action: True))

    assert registry.run("lookup_skill", name="pdf-forms") == "found pdf-forms"
    assert inner.seen == [{"name": "pdf-forms"}]


def test_the_real_skill_view_answers_a_name_argument(tmp_path: Any) -> None:
    registry = ToolRegistry()
    registry.register(SkillView(tmp_path))

    out = registry.run("skill_view", name="no-such-skill")

    assert "multiple values" not in out
    assert "no installed skill named 'no-such-skill'" in out
