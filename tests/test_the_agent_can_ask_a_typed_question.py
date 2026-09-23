"""The agent's ``decide`` tool and the MCP ``chimera_decide`` — study 22, phase 4 (part 2).

Same function as `chimera decide` and `POST /api/decide`; what is held here is what is specific to the
tool: off unless switched on (a schema in every prompt), one state or up to fifty, a bad question
answered with the reason instead of a call, and the MCP bridge listing the tool only when it has one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.decisions import Decider, Reading, as_choice
from chimera.server.mcp_server import CHIMERA_MCP_TOOLS, ChimeraMCP
from chimera.tools.decide import MAX_STATES, DecideTool, make_decide


class _Backend:
    name = "fake"
    model = "fake"

    def __init__(self) -> None:
        self.states: list[str] = []

    def instrument(self, question: Any) -> str:
        return question.instructions

    def ask(self, state: str, question: Any) -> Reading:
        self.states.append(state)
        choice = as_choice(question)
        yes = 0.9 if "ERROR" in state else 0.1
        shares = {choice.options[0]: yes, choice.options[1]: 1 - yes}
        return Reading(choice=max(shares, key=lambda k: shares[k]), shares=shares,
                       p=sum(shares[o] for o in choice.event) if choice.event else None)


Q = {"failure": {"type": "noul", "instructions": "Does the line report a failure?"}}


def test_one_state_answers_with_the_number_and_says_it_is_uncalibrated() -> None:
    out = json.loads(DecideTool(Decider(_Backend())).run(questions=Q, state="ERROR boom"))
    assert out["answers"]["failure"] == {"noul": pytest.approx(0.9)} and out["calibrated"] == {"failure": False}


def test_many_states_come_back_in_order_and_the_cap_is_enforced() -> None:
    backend = _Backend()
    out = json.loads(DecideTool(Decider(backend)).run(questions=Q, states=["ok", "ERROR x", "fine"]))
    assert [r["answers"]["failure"]["noul"] for r in out["results"]] == pytest.approx([0.1, 0.9, 0.1])
    assert DecideTool(Decider(backend)).run(questions=Q, states=["s"] * (MAX_STATES + 1)).startswith("error:")


def test_a_bad_question_is_answered_with_the_reason_and_no_call() -> None:
    backend = _Backend()
    reply = DecideTool(Decider(backend)).run(
        questions={"x": {"type": "noul", "instructions": "Is it an error and a timeout?"}}, state="s",
    )
    assert reply.startswith("error:") and "joins two conditions" in reply and backend.states == []
    assert DecideTool(Decider(backend)).run(questions=Q).startswith("error:")  # no state at all


def test_the_tool_is_off_unless_switched_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from chimera.tools.builtin import default_registry

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    for value, present in (("", False), ("1", True)):
        if value:
            monkeypatch.setenv("CHIMERA_DECIDE_TOOL", value)
        else:
            monkeypatch.delenv("CHIMERA_DECIDE_TOOL", raising=False)
        get_settings.cache_clear()
        try:
            names = set(default_registry(tmp_path, host_exec_confirm=None).names())
        finally:
            get_settings.cache_clear()
        assert ("decide" in names) is present


def test_the_mcp_bridge_lists_and_routes_decide_only_when_it_has_one() -> None:
    bare = ChimeraMCP(solve=str, fuse=str, memory_search=lambda q, k: [])
    assert [t["name"] for t in bare.tool_specs()] == [t["name"] for t in CHIMERA_MCP_TOOLS]
    with pytest.raises(KeyError):
        bare.dispatch("chimera_decide", {"questions": Q, "state": "s"})
    bridge = ChimeraMCP(solve=str, fuse=str, memory_search=lambda q, k: [], decide=make_decide(Decider(_Backend())))
    assert "chimera_decide" in [t["name"] for t in bridge.tool_specs()]
    out = json.loads(bridge.dispatch("chimera_decide", {"questions": Q, "state": "ERROR"}))
    assert out["answers"]["failure"]["noul"] == pytest.approx(0.9)
