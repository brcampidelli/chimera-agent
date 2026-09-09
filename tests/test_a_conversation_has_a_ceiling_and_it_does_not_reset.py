"""``--max-usd`` on the terminal bounds the CONVERSATION, not one answer inside it.

``chimera solve`` has taken this flag for releases and the desktop's code turn takes ``max_usd`` on
the request — where `chimera/api/code_api.py:136-139` records that the mechanism had existed with
no route reaching it. The terminal had neither, so a ``chat`` turn that looped on tools stopped at
``--max-steps`` and nowhere else.

The scope is the part worth a test. ``AgentConfig.max_usd`` would have been one line, and
``Agent.run`` builds a FRESH ``SpendBudget`` from it on every call — a cap on one answer. In a REPL
the runs are one conversation, so that resets at every prompt and a ceiling that resets is not a
ceiling. :func:`test_a_second_turn_does_not_get_the_allowance_again` is the difference, measured
against the per-turn arrangement rather than asserted about ours alone.

Everything here is free: no model call, no network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.cli.spend import BudgetedTurns, session_budget
from chimera.config import get_settings
from chimera.core.agent import Agent, AgentConfig
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.interface.session import ChatTurn, TurnReport
from chimera.orchestration.budget import SpendBudget
from chimera.providers.gateway import CompletionResult
from chimera.tools.registry import ToolRegistry

runner = CliRunner()

#: A model whose price is known and large, so one call of a few hundred tokens visibly moves a
#: ceiling. Registered rather than borrowed from the catalogue: a shipped price can change, and a
#: test whose arithmetic depends on one would then fail for a reason that is not this file's.
PRICEY = "test/expensive-model"


@pytest.fixture(autouse=True)
def _priced() -> Iterator[None]:
    set_price(PRICEY, ModelPrice(input_per_m=1.0, output_per_m=1.0))
    yield


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_CHAT_MEMORY", "CHIMERA_CASCADE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("chimera.sandbox._warned", False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _PricedBackend:
    """One model call, always the same size, always on a model with a known price."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            content="done", model=PRICEY, prompt_tokens=1000, completion_tokens=0
        )


def _agent(backend: Any, **config: Any) -> Agent:
    return Agent(backend, ToolRegistry(), AgentConfig(max_steps=2, **config))


# -- the scope: one meter for the thread ----------------------------------------------------------


def test_one_turn_is_charged_to_the_conversations_meter() -> None:
    backend = _PricedBackend()
    budget = SpendBudget(0.01)
    BudgetedTurns(_agent(backend), budget).run("hello")
    # 1000 prompt tokens at $1/M = $0.001.
    assert budget.spent == pytest.approx(0.001)
    assert backend.calls == 1


def test_a_second_turn_does_not_get_the_allowance_again() -> None:
    """The whole reason this is not ``AgentConfig.max_usd``.

    Three turns of $0.001 against a $0.002 ceiling: the third must be refused, because the first
    two used the money. The per-turn arrangement below is the control — same agent, same backend,
    same number — and it lets all three through, because each ``run`` builds its own budget and
    each turn on its own is under the cap.
    """
    backend = _PricedBackend()
    session = BudgetedTurns(_agent(backend), SpendBudget(0.002))
    for _ in range(3):
        session.run("hello")
    assert backend.calls == 2, "the third turn spent money the conversation did not have"

    per_turn = _agent(_PricedBackend(), max_usd=0.002)
    for _ in range(3):
        per_turn.run("hello")
    assert per_turn.backend.calls == 3, (  # type: ignore[attr-defined]
        "the control stopped too — then this test is not measuring the difference it claims to"
    )


def test_a_turn_over_the_ceiling_says_which_ceiling_it_hit() -> None:
    """The stop is a reason, not an error: the partial answer survives and names the cap.

    ``spend``, and the sentence says which ceiling too. ``Agent._step`` used to raise a plain
    ``BudgetExceeded`` for the dollar ceiling, so a run stopped by money reported the token
    ceiling's label through the agent and ``spend`` through ``SpendCappedBackend`` — the same
    event, two names, depending on which layer refused first.
    """
    session = BudgetedTurns(_agent(_PricedBackend()), SpendBudget(0.001))
    session.run("first")
    result = session.run("second")
    assert result.stopped_reason == "spend"
    assert "spend cap reached" in result.answer


def test_the_wrapper_still_lets_the_session_switch_models() -> None:
    """``ChatSession.set_model`` reaches for ``agent.config``; a wrapper without one breaks
    ``/model`` on every conversation that has a ceiling."""
    from chimera.interface import ChatSession

    agent = _agent(_PricedBackend())
    session = ChatSession(BudgetedTurns(agent, SpendBudget(1.0)))
    assert session.set_model("vendor/other") is True
    assert agent.config.model == "vendor/other"


# -- what a person sees ---------------------------------------------------------------------------


def _install(monkeypatch: pytest.MonkeyPatch, **script: Any) -> list[Any]:
    """A scripted ``ChatSession`` that records the runner it was handed."""
    made: list[Any] = []

    class Fake:
        max_history = 6

        def __init__(self, agent: Any = None, **kwargs: Any) -> None:
            self.agent = agent
            self.turns: list[ChatTurn] = []
            self.profile = ""
            made.append(self)

        def send_verbose(self, message: str, **_kw: Any) -> TurnReport:
            self.turns.append(ChatTurn(user=message, assistant="ok"))
            return TurnReport(
                answer="ok",
                model="fake/model",
                stopped_reason=str(script.get("stopped_reason", "final")),
            )

        def set_model(self, _slug: str | None) -> bool:
            return True

        def reset(self) -> None:
            self.turns.clear()

    monkeypatch.setattr("chimera.interface.ChatSession", Fake)
    return made


@pytest.mark.parametrize("command", ["chat", "assist"])
def test_the_flag_reaches_the_session_as_one_shared_meter(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    made = _install(monkeypatch)
    result = runner.invoke(app, [command, "--no-memory", "--max-usd", "0.5"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    runner_given = made[0].agent
    assert isinstance(runner_given, BudgetedTurns), (
        f"{command} handed the session a bare agent, so nothing counts across turns"
    )
    assert runner_given.budget.max_usd == 0.5


@pytest.mark.parametrize("command", ["chat", "assist"])
def test_without_the_flag_nothing_is_wrapped(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """No ceiling asked for, no ceiling — and no wrapper in the way of anything either."""
    made = _install(monkeypatch)
    result = runner.invoke(app, [command, "--no-memory"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    assert not isinstance(made[0].agent, BudgetedTurns)


@pytest.mark.parametrize("command", ["chat", "assist"])
def test_a_ceiling_of_zero_is_refused_before_the_first_prompt(command: str) -> None:
    """Zero is the value that fails in the dangerous direction: everything downstream reads a cap
    for truthiness, so it would say "spend nothing" and mean "spend anything"."""
    result = runner.invoke(app, [command, "--no-memory", "--max-usd", "0"], input="/exit\n")
    assert result.exit_code != 0
    assert "greater than zero" in result.output


def test_a_turn_that_was_cut_short_says_so_under_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated answer reads exactly like a finished one — the same shape as a refused command
    that the model narrates around, which is why ``stopped_reason`` is printed and not swallowed."""
    _install(monkeypatch, stopped_reason="max_steps")
    result = runner.invoke(app, ["chat", "--no-memory"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "max_steps" in "".join(result.stdout.split())


def test_an_ordinary_turn_says_nothing_about_stopping(monkeypatch: pytest.MonkeyPatch) -> None:
    """A line after every finished turn is a line nobody reads by the third one."""
    _install(monkeypatch, stopped_reason="final")
    result = runner.invoke(app, ["chat", "--no-memory"], input="hi\n/exit\n")
    assert "stopped early" not in result.stdout


def test_session_budget_treats_no_ceiling_as_no_ceiling() -> None:
    assert session_budget(None) is None
    assert session_budget(0) is None
    assert session_budget(0.25) is not None
