"""A model nobody measured compacts before its quality cliff, where a person is waiting.

The owner decided on 2026-09-27 that an exhausted context is not a stop: the run compacts and goes
on, at the context the model was measured to read (`useful_k`), and at a conservative ceiling for a
model nobody measured. The cap is opt-in per surface (`AgentConfig.cap_unmeasured`) so that every
bench, built on `AgentConfig()`, keeps the window-share budget it was measured with.

The ceiling was a fixed 64k until 2026-11-03. It is now a share of the model's own window, see
`tests/test_unmeasured_models_get_half_their_window.py`. These tests used to assert 64k as a fact;
they assert the rule now, because a fixed number was the thing that was wrong for a 1M window.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core.agent import Agent, AgentConfig
from chimera.core.context_budget import (
    DEFAULT_BUDGET_FRACTION,
    ContextBudget,
    known_window_tokens,
    unmeasured_useful_tokens,
    window_tokens,
)
from chimera.providers.catalog import CATALOG
from chimera.tools.registry import ToolRegistry

UNMEASURED = next(e for e in CATALOG if not e.useful_k and e.context_k * 1000 * 0.6 > 70_000)
MEASURED = next(e for e in CATALOG if e.useful_k)


def test_an_unmeasured_model_gets_the_ceiling_its_window_implies_when_a_surface_asks_for_it() -> None:
    window = window_tokens(UNMEASURED.slug)
    ceiling = unmeasured_useful_tokens(known_window_tokens(UNMEASURED.slug))
    budget = ContextBudget.for_model(UNMEASURED.slug, fraction=0.6, cap_unmeasured=True)

    assert int(window * 0.6) > ceiling  # the case where the cap changes anything
    assert budget.budget == ceiling
    assert budget.threshold == int(ceiling * 0.8)


def test_without_the_opt_in_the_budget_is_the_window_share_every_bench_was_measured_with() -> None:
    budget = ContextBudget.for_model(UNMEASURED.slug, fraction=0.6)

    assert budget.budget == int(window_tokens(UNMEASURED.slug) * 0.6)


def test_a_measured_model_keeps_what_was_measured_whatever_the_surface_asks() -> None:
    budget = ContextBudget.for_model(MEASURED.slug, fraction=0.6, cap_unmeasured=True)

    assert budget.useful == MEASURED.useful_k * 1000
    assert budget.budget == min(int(window_tokens(MEASURED.slug) * 0.6), MEASURED.useful_k * 1000)


def test_an_explicit_useful_still_wins_including_none_for_no_cap_at_all() -> None:
    uncapped = ContextBudget.for_model(UNMEASURED.slug, fraction=0.6, useful=None, cap_unmeasured=True)
    pinned = ContextBudget.for_model(UNMEASURED.slug, fraction=0.6, useful=50_000, cap_unmeasured=True)

    assert uncapped.budget == int(window_tokens(UNMEASURED.slug) * 0.6)
    assert pinned.budget == 50_000


def test_at_point_six_the_half_window_ceiling_binds_on_a_32k_window() -> None:
    """The case the cap was written for, and the one this test first asserted with the old 64k rule.

    At the default fraction 0.6 a 32k window gives a window share of 19,200. The rule is half the
    window, so the ceiling is 16,000 and it binds: the budget is 16,000, not 19,200. The earlier version
    of this file asserted 19,200 here because 64k did not bind on a 32k window at all; this assertion
    is the one that proves the ceiling now does something at the default fraction.
    """
    ceiling = unmeasured_useful_tokens(32_000)
    budget = ContextBudget(window=32_000, fraction=0.6, useful=ceiling)

    assert ceiling == 16_000
    assert int(32_000 * 0.6) == 19_200  # what the window share alone would have been
    assert budget.budget == 16_000
    assert budget.threshold == int(16_000 * 0.8)


def test_the_ceiling_is_a_ceiling_and_a_smaller_share_is_kept() -> None:
    """At fraction 0.3 the share (9,600) is under the 16,000 ceiling, so the ceiling must not raise it.

    Kept separate from the binding case above: the two together pin both sides of the ``min``.
    """
    ceiling = unmeasured_useful_tokens(32_000)
    budget = ContextBudget(window=32_000, fraction=0.3, useful=ceiling)

    assert budget.budget == 9_600


def test_the_agent_builds_its_budget_from_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    capped = Agent(
        object(),  # type: ignore[arg-type]
        ToolRegistry(),
        AgentConfig(model=UNMEASURED.slug, context_budget=0.6, cap_unmeasured=True),
    )
    plain = Agent(
        object(),  # type: ignore[arg-type]
        ToolRegistry(),
        AgentConfig(model=UNMEASURED.slug, context_budget=0.6),
    )

    expected = unmeasured_useful_tokens(known_window_tokens(UNMEASURED.slug))
    assert capped._budget is not None and capped._budget.budget == expected
    assert plain._budget is not None
    assert plain._budget.budget == int(window_tokens(UNMEASURED.slug) * 0.6)


def test_the_library_default_is_untouched() -> None:
    assert AgentConfig().cap_unmeasured is False
    assert AgentConfig().context_budget is None


def test_the_coding_route_compacts_by_default_and_caps_an_unmeasured_model(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.core.agent import AgentResult
    from chimera.core.context_budget import RunState
    from chimera.interface.session import ChatSession

    seen: list[AgentConfig] = []

    class _Agent:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.run_state = RunState()
            seen.append(kwargs.get("config") or args[2])

        def run(self, task: str, **kw: Any) -> AgentResult:
            return AgentResult(
                answer="ok", steps=1, stopped_reason="final", transcript=[], tool_names=[], model="m"
            )

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_Agent(None, None, AgentConfig())), workspace=ws, settings=settings)
    )
    seen.clear()

    client.post("/api/code/turn", json={"message": "go"})

    assert seen, "the route never built an agent"
    assert seen[-1].context_budget == DEFAULT_BUDGET_FRACTION  # nothing asked, and it still compacts
    assert seen[-1].cap_unmeasured is True

    seen.clear()
    client.post("/api/code/turn", json={"message": "go", "context_budget": 0.3})
    assert seen[-1].context_budget == 0.3  # a client that names a fraction gets exactly that one
