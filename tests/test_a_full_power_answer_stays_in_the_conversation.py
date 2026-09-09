"""``/task`` answered outside the thread and off the books. Both are what this covers.

The most expensive route in the terminal — a panel, a judge and a synthesizer for one ask — did two
things after producing its answer, and both were wrong.

It **appended nothing to the session**, so the answer the person had just paid a panel for was not
in the next turn's context and the follow-up question was answered by a model that had never seen
it. And it **bypassed** ``send_verbose``, so it wrote no ``usage.jsonl`` row and printed no price:
the one route the desktop's Cost screen most needed to see was the one it could not.

What it still does not do is send the conversation to the panel, and that is deliberate rather than
pending — see :func:`test_the_panel_is_asked_the_question_and_not_the_thread`.

Everything here is free: the fusion engine is replaced, so no model call and no network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.interface.session import ChatTurn, TurnReport
from chimera.providers.gateway import CompletionResult

runner = CliRunner()

PANEL = "test/panel-model"


def squashed(text: str) -> str:
    return "".join(text.split())


@pytest.fixture(autouse=True)
def _priced() -> Iterator[None]:
    set_price(PANEL, ModelPrice(input_per_m=1.0, output_per_m=1.0))
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


def _install_session(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    made: list[Any] = []

    class Fake:
        max_history = 6

        def __init__(self, agent: Any = None, **_kwargs: Any) -> None:
            self.agent = agent
            self.turns: list[ChatTurn] = []
            self.profile = ""
            made.append(self)

        def send_verbose(self, message: str, **_kw: Any) -> TurnReport:
            self.turns.append(ChatTurn(user=message, assistant="ordinary"))
            return TurnReport(answer="ordinary", model="fake/model")

        def set_model(self, _slug: str | None) -> bool:
            return True

        def reset(self) -> None:
            self.turns.clear()

    monkeypatch.setattr("chimera.interface.ChatSession", Fake)
    return made


def _install_fusion(monkeypatch: pytest.MonkeyPatch, *, tokens: int = 1000) -> list[list[Any]]:
    asked: list[list[Any]] = []

    class Fake:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def complete(self, messages: list[Any], **_kwargs: Any) -> CompletionResult:
            asked.append(messages)
            return CompletionResult(
                content="the fused answer",
                model=PANEL,
                prompt_tokens=tokens,
                completion_tokens=0,
            )

    monkeypatch.setattr("chimera.fusion.FusionEngine", Fake)
    return asked


def _usage_rows() -> list[dict[str, Any]]:
    path = get_settings().home / "usage.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# -- inside the conversation ----------------------------------------------------------------------


def test_the_fused_answer_is_part_of_the_next_turns_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = _install_session(monkeypatch)
    _install_fusion(monkeypatch)
    result = runner.invoke(app, ["assist", "--no-memory"], input="/task hard one\n/exit\n")
    assert result.exit_code == 0, result.output
    assert [(t.user, t.assistant) for t in made[0].turns] == [("hard one", "the fused answer")]


def test_what_is_recorded_is_the_models_own_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recording this is honest in a way recording a surface-written sentence would not be: the
    text in the ``assistant`` slot is replayed into every later prompt as the model's own."""
    made = _install_session(monkeypatch)
    _install_fusion(monkeypatch)
    runner.invoke(app, ["assist", "--no-memory"], input="/task hard one\n/exit\n")
    assert made[0].turns[0].assistant == "the fused answer"


def test_the_panel_is_asked_the_question_and_not_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliberate, not pending.

    ``/task`` is documented as "full-power route, one shot". Feeding six turns of history to a
    panel plus a judge plus a synthesizer multiplies the cost of the route that exists to be used
    sparingly, and somebody who wants the conversation in the prompt asks normally.
    """
    _install_session(monkeypatch)
    asked = _install_fusion(monkeypatch)
    runner.invoke(
        app, ["assist", "--no-memory"], input="an ordinary turn\n/task hard one\n/exit\n"
    )
    assert asked == [[{"role": "user", "content": "hard one"}]]


# -- on the books ---------------------------------------------------------------------------------


def test_the_turn_reaches_the_usage_log(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_session(monkeypatch)
    _install_fusion(monkeypatch)
    result = runner.invoke(app, ["assist", "--no-memory"], input="/task hard one\n/exit\n")
    assert result.exit_code == 0, result.output
    rows = _usage_rows()
    assert len(rows) == 1, "the most expensive route in the terminal is still invisible to Cost"
    assert rows[0]["model"] == PANEL
    assert rows[0]["prompt_tokens"] == 1000


def test_the_price_is_printed_under_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_session(monkeypatch)
    _install_fusion(monkeypatch)
    result = runner.invoke(app, ["assist", "--no-memory"], input="/task hard one\n/exit\n")
    out = squashed(result.stdout)
    assert squashed("in 1000") in out
    assert "$" in result.stdout


def test_it_draws_on_the_conversations_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """A route this expensive outside the meter would make the ceiling advisory."""
    _install_session(monkeypatch)
    _install_fusion(monkeypatch)
    result = runner.invoke(
        app,
        ["assist", "--no-memory", "--max-usd", "0.001"],
        input="/task one\n/task two\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert squashed("not sent") in squashed(result.stdout)


def test_an_empty_task_asks_rather_than_paying_a_panel_for_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(monkeypatch)
    asked = _install_fusion(monkeypatch)
    result = runner.invoke(app, ["assist", "--no-memory"], input="/task\n/exit\n")
    assert result.exit_code == 0, result.output
    assert asked == []
    assert squashed("usage: /task") in squashed(result.stdout)
