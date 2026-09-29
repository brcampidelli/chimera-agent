"""A model nobody measured compacts before its quality cliff, where a person is waiting.

The owner decided on 2026-09-27 that an exhausted context is not a stop: the run compacts and goes
on, at the context the model was measured to read (`useful_k`), and at a conservative 64k for a model
nobody measured. The cap is opt-in per surface (`AgentConfig.unmeasured_context_tokens`) so that
every bench, built on `AgentConfig()`, keeps the window-share budget it was measured with.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.core.context_budget import (
    DEFAULT_BUDGET_FRACTION,
    UNMEASURED_USEFUL_TOKENS,
    ContextBudget,
    window_tokens,
)
from chimera.providers.catalog import CATALOG
from chimera.tools.registry import ToolRegistry

UNMEASURED = next(e for e in CATALOG if not e.useful_k and e.context_k * 1000 * 0.6 > 70_000)
MEASURED = next(e for e in CATALOG if e.useful_k)


def test_an_unmeasured_model_gets_the_conservative_ceiling_when_a_surface_asks_for_it() -> None:
    window = window_tokens(UNMEASURED.slug)
    budget = ContextBudget.for_model(
        UNMEASURED.slug, fraction=0.6, unmeasured_cap=UNMEASURED_USEFUL_TOKENS
    )

    assert int(window * 0.6) > UNMEASURED_USEFUL_TOKENS  # the case where the cap changes anything
    assert budget.budget == UNMEASURED_USEFUL_TOKENS
    assert budget.threshold == int(UNMEASURED_USEFUL_TOKENS * 0.8)


def test_without_the_opt_in_the_budget_is_the_window_share_every_bench_was_measured_with() -> None:
    budget = ContextBudget.for_model(UNMEASURED.slug, fraction=0.6)

    assert budget.budget == int(window_tokens(UNMEASURED.slug) * 0.6)


def test_a_measured_model_keeps_what_was_measured_whatever_the_surface_asks() -> None:
    budget = ContextBudget.for_model(
        MEASURED.slug, fraction=0.6, unmeasured_cap=UNMEASURED_USEFUL_TOKENS
    )

    assert budget.useful == MEASURED.useful_k * 1000
    assert budget.budget == min(int(window_tokens(MEASURED.slug) * 0.6), MEASURED.useful_k * 1000)


def test_an_explicit_useful_still_wins_including_none_for_no_cap_at_all() -> None:
    uncapped = ContextBudget.for_model(
        UNMEASURED.slug, fraction=0.6, useful=None, unmeasured_cap=UNMEASURED_USEFUL_TOKENS
    )
    pinned = ContextBudget.for_model(
        UNMEASURED.slug, fraction=0.6, useful=50_000, unmeasured_cap=UNMEASURED_USEFUL_TOKENS
    )

    assert uncapped.budget == int(window_tokens(UNMEASURED.slug) * 0.6)
    assert pinned.budget == 50_000


def test_a_model_with_a_small_window_keeps_its_smaller_share() -> None:
    budget = ContextBudget(window=32_000, fraction=0.6, useful=UNMEASURED_USEFUL_TOKENS)

    assert budget.budget == 19_200  # the cap is a ceiling, never a floor


def test_the_agent_builds_its_budget_from_the_config() -> None:
    capped = Agent(
        object(),  # type: ignore[arg-type]
        ToolRegistry(),
        AgentConfig(
            model=UNMEASURED.slug, context_budget=0.6,
            unmeasured_context_tokens=UNMEASURED_USEFUL_TOKENS,
        ),
    )
    plain = Agent(
        object(),  # type: ignore[arg-type]
        ToolRegistry(),
        AgentConfig(model=UNMEASURED.slug, context_budget=0.6),
    )

    assert capped._budget is not None and capped._budget.budget == UNMEASURED_USEFUL_TOKENS
    assert plain._budget is not None
    assert plain._budget.budget == int(window_tokens(UNMEASURED.slug) * 0.6)


def test_the_library_default_is_untouched() -> None:
    assert AgentConfig().unmeasured_context_tokens is None
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
    assert seen[-1].unmeasured_context_tokens == UNMEASURED_USEFUL_TOKENS

    seen.clear()
    client.post("/api/code/turn", json={"message": "go", "context_budget": 0.3})
    assert seen[-1].context_budget == 0.3  # a client that names a fraction gets exactly that one
