"""An MCP server launched through a Windows `.cmd` keeps its arguments intact.

Measured in the 0.79.0 service log: the supabase connector runs `npx -y mcp-remote <url>` with
`read_only=true&features=…` in the URL. On Windows `npx` is `npx.CMD`, cmd.exe parses that command
line, and the `&` split the URL — `'read_only' não é reconhecido como um comando` — so the server never
connected. The SDK passes the arguments to CreateProcess unescaped, so the escaping has to happen
before it, in :class:`StdioMCPSession`.

The Windows tests drive a REAL `.cmd` (a batch file that runs this interpreter) through the same call
the SDK makes, because the defect lives in cmd.exe and a fake of cmd.exe would test the fake.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import anyio
import pytest

from chimera.integrations.mcp_client import StdioMCPSession
from chimera.proc.stdio import batch_safe_arguments, cmd_batch_argument, is_batch_launcher

_POSIX = os.name == "posix"


# --- the quoting itself: no Windows needed ----------------------------------------------------


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("plain-value_1.2", "plain-value_1.2"),
        ("read_only=true&features=database,docs", "read_only=true^^^&features=database,docs"),
        ("a|b", "a^^^|b"),
        ("a<b>c", "a^^^<b^^^>c"),
        ("caret^x", "caret^^^^x"),
        ("paren(x)", "paren^^^(x^^^)"),
    ],
)
def test_a_metacharacter_gets_two_carets_when_no_quoting_protects_it(
    argument: str, expected: str
) -> None:
    """The batch file parses the argument a second time, so one caret is consumed by the first pass.

    The expected strings are the spellings that come back intact through a real `.cmd`; the Windows
    test below checks the whole round trip.
    """
    assert cmd_batch_argument(argument) == expected


def test_an_argument_with_a_space_is_left_alone_because_it_is_already_quoted() -> None:
    """list2cmdline wraps a space-containing value in double quotes, and cmd.exe reads metacharacters
    literally inside quotes at both passes. A caret there would reach the child as text."""
    assert cmd_batch_argument("sp ace&amp") == "sp ace&amp"
    assert cmd_batch_argument("tab\tand&x") == "tab\tand&x"


@pytest.mark.parametrize("argument", ["100%", "%PATH%", 'say "hi"', "line\nbreak", "cr\rhere"])
def test_percent_quote_and_newline_are_refused_not_guessed(argument: str) -> None:
    """`%` is expanded by cmd.exe before any caret is read, so no escape reaches it. A double quote
    toggles cmd.exe's quote state while list2cmdline writes it as `\\"`, which cmd.exe does not
    understand. Guessing at either turns an argument into a different command line, so it is refused."""
    with pytest.raises(ValueError, match="cannot be passed through a .cmd launcher"):
        cmd_batch_argument(argument)


def test_a_non_batch_program_gets_its_arguments_back_unchanged() -> None:
    """An `.exe` is started by CreateProcess with no shell, so nothing is rewritten for it."""
    args = ["a&b", "%PATH%", 'q"uote']
    assert batch_safe_arguments(sys.executable, args) == args
    assert batch_safe_arguments("/usr/bin/npx", args) == args


@pytest.mark.skipif(_POSIX, reason="a .cmd launcher is a Windows concept")
def test_batch_launcher_detection_is_case_insensitive_on_windows() -> None:
    assert is_batch_launcher(r"C:\Program Files\nodejs\npx.CMD") is True
    assert is_batch_launcher(r"C:\tools\run.bat") is True
    assert is_batch_launcher(sys.executable) is False


def test_the_session_spawns_an_exe_with_its_configured_command_and_arguments() -> None:
    session = StdioMCPSession("definitely-not-a-real-program", ["x&y"])
    assert session._spawn_argv() == ("definitely-not-a-real-program", ["x&y"])


# --- a real .cmd on Windows ----------------------------------------------------------------------


def _fake_npx(tmp_path: Path) -> Path:
    """A batch file shaped like npx.cmd: it runs a real program with `%*`, which is the second parse."""
    launcher = tmp_path / "fake npx.cmd"  # a space in the name, as in `C:\Program Files\nodejs`
    launcher.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" -c "import sys, json; print(json.dumps(sys.argv[1:]))" %*\r\n',
        encoding="utf-8",
    )
    return launcher


@pytest.mark.skipif(_POSIX, reason="cmd.exe parses a .cmd launcher's command line only on Windows")
def test_an_ampersand_reaches_a_cmd_launcher_intact(tmp_path: Path) -> None:
    import json

    launcher = str(_fake_npx(tmp_path))
    url = "https://mcp.supabase.com/mcp?read_only=true&features=database,docs"
    spawn_command, spawn_args = StdioMCPSession(launcher, [url])._spawn_argv()

    # The same call the SDK makes on Windows (anyio.open_process with the list, no shell), wrapped by
    # anyio.run_process so the test can read the child's output.
    completed = anyio.run(anyio.run_process, [spawn_command, *spawn_args])
    received = json.loads(completed.stdout.decode("utf-8").strip())
    assert received == [url]


@pytest.mark.skipif(_POSIX, reason="cmd.exe parses a .cmd launcher's command line only on Windows")
def test_a_refused_argument_stops_the_session_before_anything_is_spawned(tmp_path: Path) -> None:
    launcher = str(_fake_npx(tmp_path))
    session = StdioMCPSession(launcher, ["100%"])
    with pytest.raises(ValueError, match="cannot be passed through a .cmd launcher"):
        session.start()
    # Refused before the loop and the thread exist, so there is nothing for close() to clean up.
    assert session._loop is None
    assert session._thread is None
