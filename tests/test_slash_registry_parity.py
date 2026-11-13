"""The three terminal surfaces take their slash commands from ONE registry, and answer all of them.

`chat`, `assist` and the full-screen app each kept a table of their own, and the tables drifted:
`/undo 2` was a command in two of them and a paid model turn in the third, and an unrecognised
`/foo` was answered by the REPL and sent to the model by the TUI. A table that is a *filter* of one
list cannot disagree with the list, but nothing stops somebody adding a command to the list for a
surface whose loop never learned it. These tests are that stop, and they are driven FROM the
registry, so a new entry is checked without anyone remembering to add a test line for it.

What is asserted per surface and per command: the surface answers it itself (it is not reported as
unknown) and it costs no model turn. What each command DOES is the business of the tests next to
the command; this file is only about whether the surface answers at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings
from chimera.interface import render
from chimera.interface.commands import (
    ASSIST,
    CHAT,
    SURFACES,
    TUI,
    command_names,
    commands_for,
    registry,
    slash_commands,
)
from chimera.interface.session import ChatTurn, TurnReport
from chimera.tui.app import ChimeraTUI

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_CHAT_MEMORY", "CHIMERA_CASCADE", "CHIMERA_COST_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("chimera.sandbox._warned", False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Session:
    """Every method a command can reach, recording the one that costs money."""

    max_history = 6

    def __init__(self, *_args: Any, **kwargs: Any) -> None:
        self.turns: list[ChatTurn] = []
        self.profile = ""
        self.remember_from_chat = bool(kwargs.get("remember_from_chat"))
        self.sent: list[str] = []
        self.model: str | None = "stub"

    def send_verbose(self, message: str, **_kw: Any) -> TurnReport:
        self.sent.append(message)
        return TurnReport(answer="ok")

    def send(self, message: str) -> str:
        return self.send_verbose(message).answer

    def reset(self) -> None:
        self.turns.clear()

    def set_model(self, slug: str | None) -> bool:
        self.model = slug
        return True

    def undo_last(self) -> None:
        return None

    def compact(self) -> int:
        return 0


class _Hand:
    """The seam a turn touches first: a call to `begin_turn` means a turn was about to start."""

    attended = False

    def __init__(self) -> None:
        self.begun: list[str] = []

    def turn_verdicts(self) -> tuple[int, int]:
        return 0, 0

    def begin_turn(self, text: str) -> None:
        self.begun.append(text)

    def taint_if_plugin_command(self, _custom: object, _text: str) -> None:
        return None


def _repl(
    monkeypatch: pytest.MonkeyPatch, surface: str, typed: str, tmp_path: Path
) -> tuple[str, list[Any]]:
    made: list[Any] = []

    class Built(_Session):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr("chimera.interface.ChatSession", Built)
    result = runner.invoke(
        app, [surface, "--no-memory", "--workspace", str(tmp_path)], input=f"{typed}\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    return result.stdout, made


async def _tui(typed: str, tmp_path: Path) -> tuple[list[str], _Session, list[str]]:
    """Type one line into the real app; return what it wrote, the session, and the turns begun."""
    from textual.widgets import Input

    session, hand = _Session(), _Hand()
    screen = ChimeraTUI(
        session,  # type: ignore[arg-type]
        model_label="stub",
        hand=hand,  # type: ignore[arg-type]
        workspace=tmp_path,
        usage_home=tmp_path,
    )
    written: list[str] = []
    original = screen._append

    def record(line: Any) -> None:
        written.append(str(line))
        original(line)

    screen._append = record  # type: ignore[method-assign]
    async with screen.run_test() as pilot:
        written.clear()  # the banner is not an answer
        screen.query_one("#prompt", Input).value = typed
        await pilot.press("enter")
        await screen.workers.wait_for_complete()
        await pilot.pause()
    return written, session, hand.begun


async def _answers(
    surface: str, name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> bool:
    """Whether ``surface`` answers ``name`` itself: not called unknown, and no model turn spent."""
    if surface == TUI:
        written, session, begun = await _tui(name, tmp_path)
        unknown = any("unknown command" in line for line in written)
        return not unknown and not session.sent and not begun
    out, made = _repl(monkeypatch, surface, name, tmp_path)
    return "unknowncommand" not in "".join(out.split()) and not made[0].sent


# -- every command in the registry is answered by every surface it is listed for ----------------


_PAIRS = [(s, c.name) for s in SURFACES for c in commands_for(s)]


@pytest.mark.parametrize(("surface", "name"), _PAIRS)
async def test_a_surface_answers_every_command_the_registry_gives_it(
    surface: str, name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert await _answers(surface, name, monkeypatch, tmp_path), (
        f"`{surface}` lists {name} in its /help and does not answer it: it is reported unknown or "
        "it reaches the model. Add the handler, or take the surface out of the registry entry."
    )


@pytest.mark.parametrize("surface", SURFACES)
async def test_the_check_can_fail(
    surface: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A command the registry does not have is exactly what an unhandled one looks like.

    Without this the test above would pass on a surface that answered nothing, which is the shape of
    guard that has already let this repository down once.
    """
    assert not await _answers(surface, "/zzz-not-a-command", monkeypatch, tmp_path)


# -- /help lists exactly what the registry gives the surface -------------------------------------


@pytest.mark.parametrize("surface", [CHAT, ASSIST])
def test_the_repl_help_lists_exactly_the_registry(
    surface: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out, made = _repl(monkeypatch, surface, "/help", tmp_path)
    # The first help line follows the prompt ("you › "): the typed line is not echoed.
    # Only what follows the prompt: the startup banner names a few commands in prose.
    body = out.split("you › ", 1)[1]
    listed = re.findall(r"(?:^|› )(/[a-z]+)\b", body, flags=re.MULTILINE)
    assert sorted(listed) == sorted(command_names(surface))
    assert made[0].sent == []


async def test_the_tui_help_lists_exactly_the_registry_except_itself(tmp_path: Path) -> None:
    shown = ChimeraTUI(
        _Session(),  # type: ignore[arg-type]
        model_label="stub",
        workspace=tmp_path,
        usage_home=tmp_path,
    )
    first = shown._help_text().splitlines()[0]
    listed = re.findall(r"(?:^|\s)(/[a-z]+)\b", first.split("[/b]", 1)[1])
    assert sorted(listed) == sorted(n for n in command_names(TUI) if n != "/help")


# -- the registry itself --------------------------------------------------------------------------


def test_the_tables_keep_the_order_each_surface_always_printed() -> None:
    assert command_names(CHAT) == [
        "/help", "/new", "/reset", "/model", "/undo", "/cost", "/compact", "/plan", "/solve",
        "/attach", "/exit",
    ]
    assert command_names(ASSIST) == [
        "/help", "/task", "/solve", "/profile", "/model", "/undo", "/cost", "/compact", "/plan",
        "/new", "/reset", "/attach", "/exit",
    ]
    assert command_names(TUI) == [
        "/model", "/new", "/reset", "/clear", "/stream", "/undo", "/cost", "/compact", "/help",
        "/exit",
    ]


def test_the_session_commands_are_on_every_surface() -> None:
    shared = {c.name for c in render.session_commands()}
    assert shared == {"/undo", "/cost", "/compact"}
    for surface in SURFACES:
        assert shared <= set(command_names(surface))


def test_a_command_exists_on_a_surface_only_by_saying_so() -> None:
    """The gaps are declared: `/plan`, `/solve` and `/attach` are not in the TUI, and say so here."""
    tui_lacks = {c.name for c in registry() if not c.on(TUI)}
    assert tui_lacks == {"/task", "/profile", "/plan", "/solve", "/attach"}
    assert {c.name for c in registry() if c.on(ASSIST) != c.on(CHAT)} == {"/task", "/profile"}


def test_the_rows_of_chat_and_assist_are_the_registry_filtered() -> None:
    from chimera.cli.commands.chat import _ATTACH_HELP, _assist_commands, _chat_commands

    assert _chat_commands() == slash_commands(CHAT)
    assert _assist_commands() == slash_commands(ASSIST)
    assert next(c for c in slash_commands(CHAT) if c.name == "/attach").help == _ATTACH_HELP
    # The two surfaces word this one differently on purpose: one starts a thread, one clears context.
    chat_new = next(c for c in slash_commands(CHAT) if c.name == "/new")
    assist_new = next(c for c in slash_commands(ASSIST) if c.name == "/new")
    assert chat_new.help != assist_new.help


def test_an_unknown_surface_is_refused_rather_than_read_as_empty() -> None:
    with pytest.raises(ValueError, match="unknown surface"):
        commands_for("desktop")
