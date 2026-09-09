"""Naming a model does something, on the surface where naming one used to do nothing.

``CascadeBackend._route`` calls the gateway with ``config.mid`` or ``config.weak`` and never with
the ``model`` it was handed (`chimera/fusion/cascade.py:144-151, 169, 189`). Under ``--cascade`` —
which is ``assist``'s default — ``--model`` and ``/model <slug>`` were therefore a value accepted
and dropped, in silence: a live check watched all eight routes land on the ladder's mid model with
``--model deepseek-chat-v3.1`` on the command line. That is the shape ``recovery``'s validation was
written against one command over.

It is honoured rather than refused, and honoured by swapping the BACKEND rather than by teaching
the cascade to obey. The ladder is a way of *choosing* a model, so naming one says the choice is
already made — and doing it at the surface keeps the blast radius at the two REPLs. Teaching
``CascadeBackend`` to prefer an explicit slug would also decide ``solve --cascade --profile
economy``, where the role models and the ladder are two deliberate mechanisms and which should win
is a question nobody has asked.

Everything here is free: no model call, no network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings
from chimera.fusion.cascade import CascadeBackend, CascadeConfig
from chimera.interface.session import ChatTurn, TurnReport
from chimera.providers.gateway import CompletionResult

runner = CliRunner()


def squashed(text: str) -> str:
    return "".join(text.split())


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


# -- the defect, pinned ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.models: list[str | None] = []

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> Any:
        self.models.append(model)
        return CompletionResult(content="ok", model=model or "unnamed")


def test_the_cascade_still_ignores_a_model_it_is_handed(tmp_path: Path) -> None:
    """Not a wish for it to change: the reason the fix is where it is.

    If this ever starts passing the slug through, the surface-level pin becomes redundant and
    should go — and this test is what would say so.
    """
    gateway = _Recorder()
    cascade = CascadeBackend(
        gateway,
        gateway,
        CascadeConfig(weak="tier/weak", mid="tier/mid", log_path=tmp_path / "routes.jsonl"),
    )
    cascade.complete([{"role": "user", "content": "hi"}], model="somebody/else", tools=[{}])
    assert gateway.models == ["tier/mid"], "the cascade honoured the slug; re-read this file"


# -- the surface ----------------------------------------------------------------------------------


def _install(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    made: list[Any] = []

    class Fake:
        max_history = 6

        def __init__(self, agent: Any = None, **_kwargs: Any) -> None:
            self.agent = agent
            self.turns: list[ChatTurn] = []
            self.profile = ""
            made.append(self)

        def send_verbose(self, message: str, **_kw: Any) -> TurnReport:
            self.turns.append(ChatTurn(user=message, assistant="ok"))
            return TurnReport(answer="ok", model="fake/model")

        def set_model(self, _slug: str | None) -> bool:
            return True

        def reset(self) -> None:
            self.turns.clear()

    monkeypatch.setattr("chimera.interface.ChatSession", Fake)
    return made


def _backend_of(made: list[Any]) -> Any:
    agent = made[0].agent
    return getattr(agent, "backend", None) or getattr(agent, "agent", agent).backend


def test_assist_on_the_ladder_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _install(monkeypatch)
    result = runner.invoke(app, ["assist", "--no-memory"], input="/exit\n")
    assert result.exit_code == 0, result.output
    assert isinstance(_backend_of(made), CascadeBackend)


def test_naming_a_model_takes_the_ladder_out_of_the_way(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _install(monkeypatch)
    result = runner.invoke(app, ["assist", "--no-memory", "-m", "vendor/chosen"], input="/exit\n")
    assert result.exit_code == 0, result.output
    assert not isinstance(_backend_of(made), CascadeBackend), (
        "the ladder is still routing, so the slug is still being dropped"
    )


def test_and_says_so_rather_than_changing_the_routing_in_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cascade is what "cheap by default" means; turning it off is worth one line."""
    _install(monkeypatch)
    result = runner.invoke(app, ["assist", "--no-memory", "-m", "vendor/chosen"], input="/exit\n")
    out = squashed(result.stdout)
    assert squashed("model pinned to vendor/chosen") in out
    assert squashed("tier cascade is off") in out


def test_chat_under_cascade_behaves_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _install(monkeypatch)
    result = runner.invoke(
        app, ["chat", "--no-memory", "--cascade", "-m", "vendor/chosen"], input="/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert not isinstance(_backend_of(made), CascadeBackend)


def test_slash_model_pins_mid_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _install(monkeypatch)
    result = runner.invoke(app, ["assist", "--no-memory"], input="/model vendor/chosen\n/exit\n")
    assert result.exit_code == 0, result.output
    assert not isinstance(_backend_of(made), CascadeBackend)
    assert squashed("pinned") in squashed(result.stdout)


def test_slash_model_with_no_argument_gives_the_ladder_its_job_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = _install(monkeypatch)
    result = runner.invoke(
        app, ["assist", "--no-memory"], input="/model vendor/chosen\n/model\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert isinstance(_backend_of(made), CascadeBackend)
    assert squashed("tier cascade picks it per turn again") in squashed(result.stdout)


def test_without_a_ladder_nothing_about_slash_model_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain ``chat`` never had this problem — the gateway honours the slug — and must not gain
    a sentence about a cascade that is not running."""
    _install(monkeypatch)
    result = runner.invoke(app, ["chat", "--no-memory"], input="/model vendor/chosen\n/exit\n")
    out = squashed(result.stdout)
    assert squashed("model → vendor/chosen") in out
    assert "pinned" not in out
