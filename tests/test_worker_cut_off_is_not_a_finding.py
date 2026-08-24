"""A worker that was cut off did not answer the question, and must not be verified as if it had.

Reproduced live against the running app with a 400-token cap, before this file existed::

    trabalhador gastou 1336 · resumo 44 caracteres · evidências 0
    veredito: verified (accepted)          <- green card
    done: fell_back=false, cancelled=false <- the run reported success

The 44 characters were the string ``delegation budget exhausted: 1336/400 tokens``. It went through
verification, came back accepted, and was handed to the synthesiser as a finding.

**Why it read as a finding.** ``Agent.run`` catches ``BudgetExceeded`` and returns the message AS
THE ANSWER — deliberately, and correctly for the coding loop: *"not an error: the run did what it
was told to do with the money it was given"*. ``RoleAgent.act`` then returned ``.answer`` and dropped
``stopped_reason``, so nothing downstream could tell a report about the run from a report about the
task. And the API path always builds workers with tools, so this was the ORDINARY case rather than a
corner: the tool-free path raises and was already handled.

The second half — that "accepted" over-claimed even for real answers — is
``test_verify_names_what_it_checked.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.orchestration.hierarchy import _CUT_OFF_REASONS
from chimera.orchestration.roles import Role, RoleAgent


@pytest.mark.parametrize("motivo", sorted(_CUT_OFF_REASONS))
def test_every_cut_off_reason_is_treated_as_one(motivo: str) -> None:
    """The set is the contract. A reason added to `AgentResult` and not to it silently regresses."""
    assert motivo in _CUT_OFF_REASONS


def test_a_finished_run_is_not_a_cut_off() -> None:
    assert "final" not in _CUT_OFF_REASONS


def test_role_agent_records_why_it_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The signal exists at the agent and was being dropped one layer above it.

    `act()` returns a bare string and eight call sites depend on that, so the reason rides on the
    instance. This is the half that was missing — everything downstream can only be as honest as
    what reaches it.
    """
    from chimera.core import agent as agent_mod

    class _Falso:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        def run(self, _task: str) -> Any:
            class R:
                answer = "delegation budget exhausted: 1336/400 tokens"
                stopped_reason = "budget"

            return R()

    monkeypatch.setattr(agent_mod, "Agent", _Falso)
    worker = RoleAgent(Role("w", "do it"), object(), tools=object())  # type: ignore[arg-type]

    saida = worker.act("summarise the file")

    assert saida.startswith("delegation budget exhausted")
    assert worker.last_stop == "budget", "the reason the loop stopped was discarded again"


def test_a_normal_answer_is_not_marked_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarding the guard: a `last_stop` hard-coded to "budget" would pass the test above.

    Without this, the fix could reject every worker and still look correct.
    """
    from chimera.core import agent as agent_mod

    class _Falso:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        def run(self, _task: str) -> Any:
            class R:
                answer = "index.html is the storefront page."
                stopped_reason = "final"

            return R()

    monkeypatch.setattr(agent_mod, "Agent", _Falso)
    worker = RoleAgent(Role("w", "do it"), object(), tools=object())  # type: ignore[arg-type]

    worker.act("summarise the file")

    assert worker.last_stop == "final"
    assert worker.last_stop not in _CUT_OFF_REASONS
