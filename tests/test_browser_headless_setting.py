"""Watching the page the agent is on — a switch that was written, wired, and unreachable.

`CHIMERA_BROWSER_HEADLESS` has existed since the browser tool shipped and `default_registry` has
always passed it to `BrowserTool`. What it never had was a way in: `patch_config`'s allowlist refused
the key, and `GET /api/config` did not report it. So the one way to see what the agent was doing on a
web page was to edit `.env` by hand and restart — a file the app never mentions.

Reachability is the whole subject here, which is why the tests are about the allowlist, the response
and the registry rather than about Chromium. No browser is launched: what broke was the wiring
between the screen and a setting that already worked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.config_api import APPLIES_WHEN, is_editable, patch_config, read_config
from chimera.config import Settings, get_settings


def test_the_screen_may_write_it() -> None:
    """It was refused, and a refusal here is invisible to the user: the row simply would not exist."""
    assert is_editable("CHIMERA_BROWSER_HEADLESS")


def test_the_screen_can_read_its_current_state(tmp_path: Path) -> None:
    headful = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_BROWSER_HEADLESS="false")  # type: ignore[arg-type]

    assert read_config(headful)["browser"] == {"headless": False}
    assert read_config(Settings(CHIMERA_HOME=str(tmp_path)))["browser"] == {"headless": True}  # type: ignore[arg-type]


def test_it_declares_that_it_waits_for_the_next_conversation() -> None:
    """The honest answer, and not the flattering one.

    The value is read once, when `default_registry` constructs the browser tool, and the tool then
    keeps whatever Chromium it launched for as long as it lives. Re-reading the setting cannot pull a
    window onto the screen for a browser already running headless, so claiming it applies to the next
    call would be a confirmation the product cannot honour.
    """
    assert APPLIES_WHEN["CHIMERA_BROWSER_HEADLESS"] == "next_conversation"


def test_a_saved_value_reaches_the_browser_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The seam that made this a feature rather than a field: `patch_config` -> settings -> the tool.

    `default_registry` is the only reader, and it takes the value from `get_settings()` — so the test
    goes through the same cache clear the endpoint relies on rather than constructing a Settings by
    hand, which is the step that would hide a stale-cache regression.
    """
    from chimera.tools.browser import BrowserTool

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()

    patch_config({"CHIMERA_BROWSER_HEADLESS": "false"}, env_path=tmp_path / ".env")
    assert get_settings().browser_headless is False

    from chimera.tools.builtin import default_registry

    browser = default_registry(tmp_path).get("browser")
    assert isinstance(browser, BrowserTool)
    assert browser._headless is False
