"""`docs/usage.md` and the three terminal surfaces have to name the same flags and slash commands.

The page describing `chat`, `assist` and `tui` was last touched in June and July and four of its
sentences had become false by September: `--fuse` "fuses deep-reasoning turns" (the router sends any
tool-carrying turn to a single model, and a REPL turn always carries tools), "Same flags as `chat`"
(the TUI has `--stream` and none of `--cascade`, `--session`, `--new`), `/reset` "clear context" (in
`chat` it has started a new thread since August), and `assist` was absent from the page entirely
while owning two commands nothing else has. Meanwhile `--session`, `--new`, `--cascade`,
`--write-region` and `/quit` were real and undocumented.

Two guards already exist near this and neither could see any of it. `tests/test_docs_links.py`
checks that links resolve; `tests/test_documented_commands_exist.py` checks a flag written
immediately after `chimera <command>` on the same line — which catches `chimera run --guard` and
misses every flag named in prose and every slash command, because a slash command is not a CLI flag
at all and appears in no snapshot.

So this file checks the two directions that were actually broken:

* **Documented, therefore real.** Every `--flag` the page attributes to a surface is in that
  command's entry in `chimera/_cli_snapshot.json` (which CI regenerates and diffs against the live
  CLI), and every `/command` it attributes to a surface is one that surface accepts.
* **Real, therefore documented.** Every long option the CLI gives those three commands, and every
  entry in the table each surface's own `/help` prints, appears on the page. This half is the one
  that would have caught `--session` and `/new`: a flag can be added, shipped and used for a month
  without a single test noticing that nobody was told.

Attribution is deliberately timid. A line that names another surface or another `chimera` command
belongs to neither side and is skipped, so a cross-reference ("prefer `chat` or `assist` when a
refusal matters") can never be read as a claim about the section it sits in. The cost is that a
flag mentioned ONLY on such a line does not count as documented — which is a failure in the safe
direction, and the reason the page states each flag once on a line about one surface.

What this does NOT check: whether the description next to a flag is true. `--fuse` existed and was
described wrongly for two months, and no mechanical check would have caught that — only reading the
router would, which is what happened.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
USAGE = ROOT / "docs" / "usage.md"
SNAPSHOT = ROOT / "chimera" / "_cli_snapshot.json"

#: The three commands that share `ChatSession` — the project's "terminal right-hand".
SURFACES = ("chat", "assist", "tui")

#: A long option, anywhere on a line. Short aliases (`-m`, `-s`) are out: one letter matches far too
#: much English, and every short option here is an alias of a long one that is checked.
_FLAG = re.compile(r"--[a-z][a-z0-9-]*")

#: A slash command as the page writes one: inside inline code, so `solve`/`project` (a backtick, a
#: slash, a backtick) and `verify/revert` are not read as commands.
_DOC_COMMAND = re.compile(r"`(/[a-z][a-z0-9_-]*)")

#: A slash command as the SOURCE writes one: a string literal that starts with it. Anchored on the
#: quote so Rich's closing markup (`[/dim]`, `[/red]`) is not mistaken for a command.
_SRC_COMMAND = re.compile(r"[\"'](/[a-z][a-z0-9_-]*)")

#: `chimera <name>` — used to notice that a line is talking about a different command.
_INVOCATION = re.compile(r"\bchimera\s+([a-z][a-z0-9-]*)")

#: A surface named as inline code: "prefer `chat` or `assist`".
_SURFACE_MENTION = re.compile(r"`(chat|assist|tui)`")


# --- what the CLI has -----------------------------------------------------------------------------


def _snapshot() -> dict[str, dict]:
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return {command["name"]: command for command in data["commands"]}


def real_flags(surface: str) -> set[str]:
    """Every long option `chimera <surface>` accepts, including the `--no-x` half of a toggle."""
    command = _snapshot()[surface]
    flags: set[str] = set()
    for param in command["params"]:
        flags.update(o for o in param.get("opts", []) if o.startswith("--"))
        flags.update(o for o in param.get("secondary_opts", []) if o.startswith("--"))
    return flags


def _table(surface: str) -> set[str]:
    """The commands that surface's own `/help` lists — the table, not the aliases."""
    if surface == "tui":
        from chimera.tui.app import _SLASH

        return {entry.strip() for entry in _SLASH}
    from chimera.cli import main

    builder = main._chat_commands if surface == "chat" else main._assist_commands
    return {command.name for command in builder()}


def accepted_commands(surface: str) -> set[str]:
    """Everything the surface answers itself, table plus aliases, read out of its own source.

    `/quit` and `/q` are handled before the table is consulted and appear in no table, so a set
    built from `_chat_commands()` alone would call two real commands imaginary.
    """
    if surface == "tui":
        source = (ROOT / "chimera" / "tui" / "app.py").read_text(encoding="utf-8")
    else:
        from chimera.cli import main

        body = getattr(main, surface)
        builder = main._chat_commands if surface == "chat" else main._assist_commands
        source = inspect.getsource(body) + inspect.getsource(builder)
    return set(_SRC_COMMAND.findall(source)) | _table(surface)


# --- what the page says ---------------------------------------------------------------------------


def _section(surface: str) -> list[str]:
    """The lines of `### \\`<surface>\\` — …` up to the next `### ` heading."""
    lines = USAGE.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(f"### `{surface}`")),
        None,
    )
    assert start is not None, f"docs/usage.md has no `### \\`{surface}\\`` section"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("### ")), len(lines)
    )
    return lines[start:end]


def _is_about(line: str, surface: str, commands: frozenset[str]) -> bool:
    """Whether this line is talking about `surface` only.

    A line that names another command or another surface is ambiguous and belongs to nobody: the
    alternative is reading "`chimera chat`'s `--cascade` has no equivalent here", inside the `tui`
    section, as the claim that `tui` takes `--cascade`.
    """
    named = {name for name in _INVOCATION.findall(line) if name in commands}
    named |= set(_SURFACE_MENTION.findall(line))
    return not (named - {surface})


def documented(surface: str) -> tuple[set[str], set[str]]:
    """`(flags, slash commands)` the page attributes to this surface, ambiguous lines skipped."""
    commands = frozenset(_snapshot())
    flags: set[str] = set()
    slashes: set[str] = set()
    for line in _section(surface):
        if not _is_about(line, surface, commands):
            continue
        flags.update(_FLAG.findall(line))
        slashes.update(_DOC_COMMAND.findall(line))
    return flags, slashes


# --- the guard ------------------------------------------------------------------------------------


@pytest.mark.parametrize("surface", SURFACES)
def test_every_flag_the_page_gives_a_surface_is_one_that_surface_has(surface: str) -> None:
    flags, _ = documented(surface)
    invented = sorted(flags - real_flags(surface))
    assert not invented, (
        f"docs/usage.md gives `chimera {surface}` flags it does not have: {invented}. "
        f"It accepts {sorted(real_flags(surface))}. Either the flag was renamed and the page was "
        "not, or the sentence is about another command and should name it."
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_every_flag_a_surface_has_is_on_the_page(surface: str) -> None:
    flags, _ = documented(surface)
    undocumented = sorted(real_flags(surface) - flags)
    assert not undocumented, (
        f"`chimera {surface}` accepts {undocumented} and docs/usage.md never says so. "
        "Add each one to that command's section, on a line that names no other surface — "
        "`--session`, `--new` and `--cascade` shipped and went undocumented for a month."
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_every_slash_command_the_page_gives_a_surface_is_one_it_answers(surface: str) -> None:
    _, slashes = documented(surface)
    invented = sorted(slashes - accepted_commands(surface))
    assert not invented, (
        f"docs/usage.md gives `chimera {surface}` slash commands it does not answer: {invented}. "
        f"It answers {sorted(accepted_commands(surface))}."
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_every_slash_command_a_surface_lists_in_help_is_on_the_page(surface: str) -> None:
    _, slashes = documented(surface)
    undocumented = sorted(_table(surface) - slashes)
    assert not undocumented, (
        f"`chimera {surface}` offers {undocumented} in its own /help and docs/usage.md does not "
        "list them. The two tables are for the same person."
    )


# --- the guard cannot be vacuous ------------------------------------------------------------------


@pytest.mark.parametrize("surface", SURFACES)
def test_the_page_is_actually_being_read(surface: str) -> None:
    """A section that stopped parsing would make every assertion above pass on an empty set."""
    flags, slashes = documented(surface)
    assert len(flags) >= 5, f"only {len(flags)} flags found in the {surface} section"
    assert len(slashes) >= 4, f"only {len(slashes)} slash commands found in the {surface} section"


def test_the_surfaces_are_the_shape_this_file_assumes() -> None:
    """Pin the two facts the parsing rests on, so a reshaped snapshot fails loudly."""
    assert "--session" in real_flags("chat"), "the snapshot lost chat's options"
    assert "--no-stream" in real_flags("tui"), "secondary_opts are no longer read"
    for surface in SURFACES:
        assert "/help" in accepted_commands(surface)
        assert {"/quit", "/q"} <= accepted_commands(surface), (
            f"{surface} answers /quit and /q; the source scan stopped seeing them"
        )


def test_an_ambiguous_line_belongs_to_nobody() -> None:
    """The rule that lets one section talk about another without lying about itself."""
    commands = frozenset(_snapshot())
    assert not _is_about("the `--cascade` of `chimera chat`", "tui", commands)
    assert not _is_about("prefer `chat` or `assist` here", "tui", commands)
    assert _is_about("uv run chimera tui --no-stream", "tui", commands)
    assert _is_about("`--stream` toggles live tokens", "tui", commands)
