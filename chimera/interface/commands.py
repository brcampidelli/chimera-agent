"""One registry of slash commands for the three terminal surfaces: ``chat``, ``assist`` and the TUI.

Each surface used to own a table (``_chat_commands()``, ``_assist_commands()``, and ``_SLASH`` plus
``_HELP`` in the TUI), and the tables drifted: ``/undo`` existed on the Code screen and in none of
them, ``/undo 2`` was a command in two surfaces and a paid model turn in the third. A command is now
declared ONCE, with the surfaces it exists on, and every table is a filter of this list.

What this does not do is dispatch. Each surface still answers its own commands, because what
``/new`` does differs (a new thread, or a cleared context), and the parity test
(``tests/test_slash_registry_parity.py``) is what fails when a command is listed here for a surface
that does not answer it.

``render.SlashCommand`` and ``render.session_commands()`` keep their shape: the three session
commands are read from there rather than written a second time.

Imported late by ``chimera.cli.commands.chat``: the CLI pays for every module it imports at start.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from chimera.interface.render import SlashCommand, session_commands

CHAT = "chat"
ASSIST = "assist"
TUI = "tui"
SURFACES = (CHAT, ASSIST, TUI)

_ATTACH_HELP = "attach a document to your next message; the answer is checked against it"
_SOLVE_HELP = "hand it to the verified loop: plan, verify, revert on failure"
_PLAN_HELP = "show a plan first; run the task only once you approve it"


@dataclass(frozen=True)
class RegisteredCommand:
    """A command, the surfaces it exists on, and where it sits in each surface's table.

    ``rank`` maps a surface to the position of the command in that surface's table, so the order
    each ``/help`` has always printed is reproduced rather than re-decided here. ``help_by`` holds
    the surfaces whose wording differs (``/new`` starts a thread in ``chat`` and clears the context
    in ``assist``); ``terse`` is the few words the TUI's one-line help adds after the name.
    """

    name: str
    argument: str = ""
    help: str = ""
    rank: Mapping[str, int] = field(default_factory=dict)
    help_by: Mapping[str, str] = field(default_factory=dict)
    terse: str = ""

    def on(self, surface: str) -> bool:
        return surface in self.rank

    def row(self, surface: str) -> SlashCommand:
        return SlashCommand(self.name, self.argument, self.help_by.get(surface, self.help))


def _entry(
    name: str,
    argument: str = "",
    help: str = "",  # the field is called `help` everywhere else too
    *,
    chat: int | None = None,
    assist: int | None = None,
    tui: int | None = None,
    help_by: Mapping[str, str] | None = None,
    terse: str = "",
) -> RegisteredCommand:
    ranks = {s: r for s, r in ((CHAT, chat), (ASSIST, assist), (TUI, tui)) if r is not None}
    return RegisteredCommand(
        name, argument, help, MappingProxyType(ranks), MappingProxyType(dict(help_by or {})), terse
    )


def registry() -> tuple[RegisteredCommand, ...]:
    """Every built-in slash command, on every surface that has it. Built on call: see the module."""
    session = {command.name: command for command in session_commands()}
    undo, cost, compact = session["/undo"], session["/cost"], session["/compact"]
    return (
        _entry("/help", "", "this list", chat=1, assist=1, tui=9),
        _entry(
            "/task", "<hard ask>", "full-power fusion route, one shot", assist=2
        ),
        _entry("/profile", "<kind>: <fact>", "remember a fact about you", assist=4),
        _entry(
            "/new",
            "",
            "start a fresh thread (the current one stays saved)",
            chat=2,
            assist=10,
            tui=2,
            help_by={ASSIST: "clear the conversation context (same as /reset)"},
            terse="(fresh thread)",
        ),
        _entry(
            "/reset",
            "",
            "same as /new — the transcript is on disk now",
            chat=3,
            assist=11,
            tui=3,
            help_by={ASSIST: "clear the conversation context (nothing is deleted)"},
            terse="(same)",
        ),
        _entry(
            "/model",
            "<slug>",
            "switch model (no argument = back to default)",
            chat=4,
            assist=5,
            tui=1,
        ),
        _entry("/clear", "", "clear the screen", tui=4, terse="(clear screen)"),
        _entry("/stream", "", "toggle live tokens", tui=5, terse="(toggle live tokens)"),
        _entry(undo.name, undo.argument, undo.help, chat=5, assist=6, tui=6),
        _entry(cost.name, cost.argument, cost.help, chat=6, assist=7, tui=7),
        _entry(compact.name, compact.argument, compact.help, chat=7, assist=8, tui=8),
        _entry("/plan", "<task>", _PLAN_HELP, chat=8, assist=9),
        _entry("/solve", "<task>", _SOLVE_HELP, chat=9, assist=3),
        _entry("/attach", "<file>", _ATTACH_HELP, chat=10, assist=12),
        _entry("/exit", "", "quit (also /quit, /q)", chat=11, assist=13, tui=10),
    )


def commands_for(surface: str) -> list[RegisteredCommand]:
    """The registry entries that exist on ``surface``, in the order that surface prints them."""
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r}; expected one of {SURFACES}")
    found = [command for command in registry() if command.on(surface)]
    return sorted(found, key=lambda command: command.rank[surface])


def slash_commands(surface: str) -> list[SlashCommand]:
    """``surface``'s table as ``/help`` and the unknown-command message print it."""
    return [command.row(surface) for command in commands_for(surface)]


def command_names(surface: str) -> list[str]:
    return [command.name for command in commands_for(surface)]
