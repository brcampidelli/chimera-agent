"""The plan gate, pinned at the one place it could go wrong: by becoming permission.

`chimera/api/plan_gate.py` stops a coding turn on its plan before anything runs. The evidence for
doing that is human-subject and strong (arXiv 2604.04918: exposure 88.5% under action-confirmation
against 60.4% under plan-gating, p<.001). The evidence for the failure mode is just as strong and
points the other way: arXiv 2608.27443 (n=113, pre-registered) measured overreach blocked falling
from 59.6% to **39.6%** when users could pre-authorise what an agent may do, because permission
granted in advance is a per-action gate switched off.

So the gate is only ever an EXTRA stop, and these tests are what makes that structural rather than
a claim in a docstring:

* it cannot reach the governance layer at all — an AST walk, the same shape
  `test_the_judge_is_a_library.py` uses for the judge;
* it fails CLOSED when our own planner produces nothing, because the one case where our machinery
  broke must not be the case that runs unsupervised;
* the note it injects tells the model which steps a person approved and explicitly does NOT tell it
  those steps are permitted.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.api import plan_gate
from chimera.core.planner import Plan

SOURCE = Path(plan_gate.__file__)

#: The only governance module the gate is allowed to touch, and what for. `pending` IS the shared
#: approval mechanism — reusing it is why the plan question appears in the desktop dialog, in
#: `chimera approve`, in the durable history and under the same silence-refuses clock. Anything
#: else in `chimera.governance` is the enforcement path, and a gate that could import it is a gate
#: that could weaken it.
ALLOWED_GOVERNANCE: dict[str, str] = {
    "chimera.governance.pending": "the shared durable-approval mechanism",
}


@dataclass
class _Reply:
    content: str


class _Backend:
    """A planner backend that returns whatever it was handed, and records the call."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> _Reply:
        self.calls.append({"messages": messages, **kwargs})
        return _Reply(self.text)


def _ask(answer: bool, seen: list[dict[str, Any]] | None = None) -> Any:
    def ask(home: Any, action: str, reason: str, **kwargs: Any) -> bool:
        if seen is not None:
            seen.append({"home": home, "action": action, "reason": reason, **kwargs})
        return answer

    return ask


def test_the_gate_cannot_reach_the_enforcement_path() -> None:
    """An AST walk over the module: no import of `chimera.governance.*` beyond the approval one.

    Inspection would have passed on the day this was written and says nothing about the day someone
    adds `from chimera.governance.policy import RuleSet` to "let an approved plan skip the obvious
    ones". That edit is the whole risk, and this is what it has to get past.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    reached: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            reached.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            reached.add(node.module)

    governance = {m for m in reached if m.startswith("chimera.governance")}
    assert governance <= set(ALLOWED_GOVERNANCE), (
        "the plan gate reached into governance beyond the approval mechanism: "
        f"{sorted(governance - set(ALLOWED_GOVERNANCE))}. The gate ADDS a stop; it must have no "
        "way to remove one."
    )


def test_a_planner_that_returns_nothing_does_not_start_the_turn() -> None:
    verdict = plan_gate.gate(
        "fix the parser",
        home=Path("."),
        backend=_Backend("   \n  \n"),
        wait_seconds=1.0,
        ask=_ask(True),
    )
    assert verdict.approved is False
    assert verdict.outcome == "no_plan"
    assert verdict.plan is None


def test_a_refusal_stops_the_turn_and_is_reported_as_a_refusal() -> None:
    verdict = plan_gate.gate(
        "fix the parser",
        home=Path("."),
        backend=_Backend("1. read the file\n2. change it"),
        wait_seconds=1.0,
        ask=_ask(False),
    )
    assert verdict.approved is False
    assert verdict.outcome == "refused"
    assert verdict.plan is not None, "a refused plan is still shown in the receipt"


def test_the_question_carries_the_plan_and_says_what_approving_does_not_do() -> None:
    seen: list[dict[str, Any]] = []
    shown: list[Plan] = []
    verdict = plan_gate.gate(
        "fix the parser",
        home=Path("/somewhere"),
        backend=_Backend("1. read src/parser.py\n2. add a test"),
        wait_seconds=42.0,
        on_plan=shown.append,
        ask=_ask(True, seen),
    )
    assert verdict.approved is True
    assert [p.steps for p in shown] == [["read src/parser.py", "add a test"]]

    (asked,) = seen
    assert "read src/parser.py" in asked["action"], "the person is asked about the plan itself"
    assert asked["decision"] == "review"
    assert asked["wait_seconds"] == 42.0
    # The sentence a person reads before deciding. If approving ever silently pre-approves the
    # actions, this promise is the thing that became false.
    assert "does NOT pre-approve" in asked["reason"]
    assert "still asks separately" in asked["reason"]


def test_the_injected_note_names_the_steps_without_granting_them() -> None:
    note = plan_gate.as_system_note(Plan(steps=["read src/parser.py", "add a test"]))
    assert "read src/parser.py" in note
    assert "approved this plan" in note
    # The model must not be told the steps are cleared to run: every dangerous one still asks.
    assert "still be asked about individually" in note


def test_the_planning_call_is_tool_free_and_asks_for_what_makes_a_plan_refusable() -> None:
    """One `complete` call, no registry, and a prompt that asks for files and commands.

    A plan a person cannot refuse on is not a gate. "Improve the code" and "rewrite auth.py and run
    the migration" are both plans; only one of them can be answered.
    """
    backend = _Backend("1. edit src/a.py\n2. run pytest")
    plan_gate.gate(
        "fix it", home=Path("."), backend=backend, wait_seconds=1.0, ask=_ask(True)
    )
    (call,) = backend.calls
    system = call["messages"][0].content
    assert "Name the files you expect to change" in system
    assert "command you expect to run" in system
    assert "registry" not in call, "the planning call takes no tools"
