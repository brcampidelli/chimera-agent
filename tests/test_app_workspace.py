"""Where the desktop app roots its tools — and why the friendly-looking default is the wrong one.

``chimera app`` in a terminal inherits the directory you were standing in, so falling back to the
current directory is right by accident. A packaged build inherits wherever its shortcut points:
``C:\\Program Files\\Chimera`` for an installed app, which is not the user's project and is not
writable without elevation, so the agent's edits fail there in a way that reads like a bug in the
agent. ``$CHIMERA_WORKSPACE`` is the lever that was missing — there was no way at all to say it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.cli.main import resolve_app_workspace


def test_the_flag_wins(monkeypatch: Any) -> None:
    monkeypatch.setenv("CHIMERA_WORKSPACE", "/from/env")
    assert resolve_app_workspace("/from/flag") == Path("/from/flag")


def test_the_environment_is_used_when_no_flag_is_given(monkeypatch: Any) -> None:
    monkeypatch.setenv("CHIMERA_WORKSPACE", "/from/env")
    assert resolve_app_workspace(None) == Path("/from/env")


def test_it_falls_back_to_the_current_directory(monkeypatch: Any) -> None:
    monkeypatch.delenv("CHIMERA_WORKSPACE", raising=False)
    assert resolve_app_workspace(None) == Path(".")


def test_an_empty_environment_value_does_not_count_as_a_choice(monkeypatch: Any) -> None:
    """``CHIMERA_WORKSPACE=`` is how an env var looks when someone MEANT to unset it. Rooting the
    agent at `Path("")` — which resolves to the current directory anyway, but silently — would hide
    that; treating empty as absent keeps the two indistinguishable cases actually indistinguishable."""
    monkeypatch.setenv("CHIMERA_WORKSPACE", "")
    assert resolve_app_workspace(None) == Path(".")


def test_it_never_reaches_for_the_home_directory(monkeypatch: Any) -> None:
    """The tempting default, refused on purpose.

    Home is writable and would look friendlier than an install directory. But "the agent may edit
    anything under $HOME" is not a default anyone chose, and reach is the one setting that has to be
    arrived at deliberately rather than inherited. A visibly wrong root the user corrects is safer
    than a large plausible one they never agreed to.
    """
    monkeypatch.delenv("CHIMERA_WORKSPACE", raising=False)
    monkeypatch.setenv("HOME", "/home/someone")
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\someone")

    assert resolve_app_workspace(None) != Path.home()
