"""``/solve`` — the terminal's version of the desktop's second button.

`chimera/api/code_api.py:1215-1221` states the difference between the two buttons on the Code
screen: Send edits your files and keeps whatever it wrote; "Run with verification" plans, verifies
and can undo. The terminal had only the first, on all three surfaces.

The construction is **reused, not copied**: this calls the ``solve`` command itself, with every one
of its parameters, read off its own signature. A second ``AutonomousAgent`` assembled here would be
a weaker product wearing the same name the day the two drift, and there is nothing smaller to share
— ``solve`` builds its worker, planner, manager, verifier, checkpointer and six learning seams
inside one closure over fifty flags.

Three properties are the reason this is a command and not a behaviour: it is **never automatic**,
it **says what it is about to do** before it does it, and it draws on the **conversation's**
ceiling rather than a fresh one.

Everything here is free: ``solve`` itself is replaced, so no model call and no network.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from chimera.cli.main import SolveFailed, _solve_defaults, app, solve
from chimera.config import get_settings
from chimera.interface.session import ChatTurn, TurnReport

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


# -- the argument list cannot go stale ------------------------------------------------------------


def test_every_parameter_solve_takes_is_given_a_real_value() -> None:
    """The trap that killed the documented ``tui`` fallback, one command over.

    A Typer command called as a plain function hands the parameters you omit their
    ``typer.OptionInfo`` DEFAULT OBJECTS, not their defaults — and ``bool(OptionInfo)`` is True, so
    every flag would arrive switched on. Reading the defaults off the signature is what makes this
    impossible to leave stale when somebody adds the fifty-third parameter.
    """
    from typer.models import ParameterInfo

    defaults = _solve_defaults()
    assert set(defaults) == set(inspect.signature(solve).parameters)
    leaked = sorted(k for k, v in defaults.items() if isinstance(v, ParameterInfo))
    assert not leaked, f"these would arrive as OptionInfo objects: {leaked}"


def test_the_defaults_are_solves_own() -> None:
    """Not a second opinion about how a run should be configured: ``solve``'s."""
    defaults = _solve_defaults()
    assert defaults["max_attempts"] == 3
    assert defaults["recovery"] == "generic"
    assert defaults["collect"] is True
    assert defaults["verify"] is None


# -- driving the REPL -----------------------------------------------------------------------------


@dataclass
class _Attempt:
    usd: float | None = 0.0


@dataclass
class _Result:
    answer: str = "the loop's own answer"
    success: bool = True
    attempts: list[_Attempt] = field(default_factory=lambda: [_Attempt(0.004)])


def _install_session(monkeypatch: pytest.MonkeyPatch, seed: list[ChatTurn] | None = None) -> list[Any]:
    made: list[Any] = []

    class Fake:
        max_history = 6

        def __init__(self, agent: Any = None, **_kwargs: Any) -> None:
            self.agent = agent
            self.turns: list[ChatTurn] = list(seed or [])
            self.profile = ""
            made.append(self)

        def send_verbose(self, message: str, **_kw: Any) -> TurnReport:
            self.turns.append(ChatTurn(user=message, assistant="ok"))
            return TurnReport(answer="ok", model="fake/model")

        def set_model(self, slug: str | None) -> bool:
            """What the real one does: write the slug onto the agent's config.

            Modelled rather than stubbed to True, because ``/solve`` reads the model back from
            exactly that field — a fake that only returned True would let a test pass while the
            REPL sent the wrong model to a run that edits files.
            """
            config = getattr(self.agent, "config", None)
            if config is None:
                return False
            config.model = slug
            return True

        def reset(self) -> None:
            self.turns.clear()

    monkeypatch.setattr("chimera.interface.ChatSession", Fake)
    return made


def _install_solve(
    monkeypatch: pytest.MonkeyPatch, *, result: Any = None, raises: BaseException | None = None
) -> list[dict[str, Any]]:
    """Replace the command with a recorder. Everything up to the call is the shipped code."""
    seen: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> Any:
        seen.append(kwargs)
        if raises is not None:
            raise raises
        return result if result is not None else _Result()

    monkeypatch.setattr("chimera.cli.main.solve", fake)
    return seen


@pytest.mark.parametrize("command", ["chat", "assist"])
def test_the_slash_command_reaches_the_loop_with_the_conversations_workspace(
    monkeypatch: pytest.MonkeyPatch, command: str, tmp_path: Path
) -> None:
    _install_session(monkeypatch)
    seen = _install_solve(monkeypatch)
    ws = str(tmp_path / "repo")
    result = runner.invoke(
        app,
        [command, "--no-memory", "-w", ws, "--write-region", "src/**"],
        input="/solve fix the parser\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert len(seen) == 1, "the REPL did not reach the verified loop"
    assert seen[0]["task"] == "fix the parser"
    assert seen[0]["workspace"] == ws
    assert seen[0]["write_region"] == "src/**"


def test_it_says_what_it_is_about_to_do_before_it_does_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It edits files and can spend several attempts' worth of money; a person gets to see the
    task and the shape of the run first."""
    _install_session(monkeypatch)
    _install_solve(monkeypatch)
    result = runner.invoke(
        app, ["chat", "--no-memory"], input="/solve fix the parser\n/exit\n"
    )
    out = squashed(result.stdout)
    assert squashed("handing this to the verified loop") in out
    assert squashed("verify-or-revert") in out
    assert squashed("fix the parser") in out


def test_it_never_runs_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary message is an ordinary turn. This is the whole reason it is a slash command."""
    _install_session(monkeypatch)
    seen = _install_solve(monkeypatch)
    result = runner.invoke(
        app, ["chat", "--no-memory"], input="please fix the parser and verify it\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert seen == []


def test_the_loops_own_answer_lands_in_the_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """So the next turn knows what happened — and the recorded text is the LOOP's, not a sentence
    the surface wrote about it."""
    made = _install_session(monkeypatch)
    _install_solve(monkeypatch, result=_Result(answer="patched three files"))
    result = runner.invoke(app, ["chat", "--no-memory"], input="/solve go\n/exit\n")
    assert result.exit_code == 0, result.output
    assert made[0].turns[-1].assistant == "patched three files"


def test_a_failed_run_keeps_the_repl_and_keeps_its_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``solve`` exits 1 on failure. Inside a REPL that would have ended the session mid-thread —
    and a failed run has an answer worth keeping."""
    made = _install_session(monkeypatch)
    _install_solve(
        monkeypatch, raises=SolveFailed(_Result(answer="could not make the tests pass", success=False))
    )
    result = runner.invoke(app, ["chat", "--no-memory"], input="/solve go\nhello\n/exit\n")
    assert result.exit_code == 0, result.output
    assert made[0].turns[0].assistant == "could not make the tests pass"
    assert made[0].turns[-1].user == "hello", "the REPL died on the failed run"


def test_a_refusal_from_solve_itself_does_not_end_the_repl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``solve`` refuses some flag combinations before it runs; it has already said which."""
    made = _install_session(monkeypatch)
    _install_solve(monkeypatch, raises=typer.Exit(code=1))
    result = runner.invoke(app, ["chat", "--no-memory"], input="/solve go\nhello\n/exit\n")
    assert result.exit_code == 0, result.output
    assert [t.user for t in made[0].turns] == ["hello"]


def test_with_no_argument_it_takes_the_last_thing_you_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(monkeypatch)
    seen = _install_solve(monkeypatch)
    result = runner.invoke(
        app, ["chat", "--no-memory"], input="make the retries idempotent\n/solve\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert seen[0]["task"] == "make the retries idempotent"


def test_it_runs_on_the_model_the_conversation_is_using(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/model`` writes to the agent's config; ``/solve`` reads it there rather than from the
    command line, so a model pinned mid-conversation is the one the run uses."""
    _install_session(monkeypatch)
    seen = _install_solve(monkeypatch)
    result = runner.invoke(
        app,
        ["chat", "--no-memory", "-m", "vendor/launched"],
        input="/model vendor/pinned\n/solve go\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert seen[0]["model"] == "vendor/pinned"


def test_with_nothing_to_go_on_it_asks_rather_than_guesses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(monkeypatch)
    seen = _install_solve(monkeypatch)
    result = runner.invoke(app, ["chat", "--no-memory"], input="/solve\n/exit\n")
    assert result.exit_code == 0, result.output
    assert seen == []
    assert squashed("usage: /solve") in squashed(result.stdout)


# -- the ceiling from item 1 applies to it --------------------------------------------------------


def test_the_run_is_capped_by_what_the_conversation_has_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(monkeypatch)
    seen = _install_solve(monkeypatch)
    result = runner.invoke(
        app, ["chat", "--no-memory", "--max-usd", "0.20"], input="/solve go\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert seen[0]["max_usd"] == pytest.approx(0.20)


def test_a_second_run_only_gets_what_the_first_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of charging the conversation's meter. Without it, a command anybody can type
    twice hands out the whole allowance every time and the ceiling is not one."""
    _install_session(monkeypatch)
    seen = _install_solve(monkeypatch, result=_Result(attempts=[_Attempt(0.05)]))
    result = runner.invoke(
        app, ["chat", "--no-memory", "--max-usd", "0.20"], input="/solve a\n/solve b\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert seen[0]["max_usd"] == pytest.approx(0.20)
    assert seen[1]["max_usd"] == pytest.approx(0.15)


def test_a_run_whose_price_is_unknown_stops_the_ceiling_rather_than_reading_as_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ceiling that skips what it cannot price shows green while the real spend climbs."""
    _install_session(monkeypatch)
    seen = _install_solve(monkeypatch, result=_Result(attempts=[_Attempt(None)]))
    result = runner.invoke(
        app, ["chat", "--no-memory", "--max-usd", "0.20"], input="/solve a\n/solve b\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert len(seen) == 1, "the second run was allowed on money nobody could count"
    assert squashed("not sent") in squashed(result.stdout)


def test_with_no_ceiling_the_run_is_uncapped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_session(monkeypatch)
    seen = _install_solve(monkeypatch)
    result = runner.invoke(app, ["chat", "--no-memory"], input="/solve go\n/exit\n")
    assert result.exit_code == 0, result.output
    assert seen[0]["max_usd"] is None
