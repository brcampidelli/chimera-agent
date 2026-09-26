"""The browser situation module (study 25, S11) reaches the prompt only with its flag AND the browser.

Two conditions, each for its own reason. The flag, because the module is unmeasured as a whole and a
run that has not asked for it must send the bytes it sent before. The tool, because a sentence about
a browser the session was not given invites a call to nothing — the rule `TODO_PROMPT` already
follows. The registry's half of the module (`BrowserTool(situation=...)`) reads the same setting, so
one switch turns both on and off together.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core import Agent, AgentConfig
from chimera.core.agent import DEFAULT_SYSTEM_PROMPT
from chimera.prompts.snapshots import render_all
from chimera.tools import ToolRegistry
from chimera.tools.browser import BrowserTool
from chimera.tools.browser_situation import BROWSER_SITUATION_PROMPT, BrowserSituation
from chimera.tools.builtin import EchoTool


class _NoModel:
    def complete(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - never reached
        raise AssertionError("composing a prompt must not call the model")


def _tools(*, browser: bool) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    if browser:
        registry.register(BrowserTool())
    return registry


def _prompt(*, flag: bool, browser: bool) -> str:
    config = AgentConfig(inject_skill_context=False, prefix_nonce="", browser_situation=flag)
    return Agent(_NoModel(), _tools(browser=browser), config).compose_system_prompt("find a price")


@pytest.mark.parametrize(
    ("flag", "browser", "present"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_the_module_needs_both_the_flag_and_the_browser(flag: bool, browser: bool, present: bool) -> None:
    assert (BROWSER_SITUATION_PROMPT in _prompt(flag=flag, browser=browser)) is present


def test_off_the_prompt_is_the_bytes_it_was() -> None:
    """With the flag off, a session holding the browser sends exactly what a session without it does."""
    assert _prompt(flag=False, browser=True) == _prompt(flag=False, browser=False)


def test_the_flag_defaults_off_and_follows_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_BROWSER_SITUATION", raising=False)
    get_settings.cache_clear()
    assert AgentConfig().browser_situation is False
    monkeypatch.setenv("CHIMERA_BROWSER_SITUATION", "1")
    get_settings.cache_clear()
    assert AgentConfig().browser_situation is True


def test_the_module_sits_after_the_core_and_before_the_project_and_the_owner() -> None:
    """The plan's precedence: owner > situation contract > project convention, the owner read last."""
    text = render_all()["assembled.loop_browser_situation"]
    positions = [
        text.index(DEFAULT_SYSTEM_PROMPT),
        text.index(BROWSER_SITUATION_PROMPT),
        text.index("Project instructions"),
        text.index("Instructions from the person who runs you"),
    ]
    assert positions == sorted(positions)


def _browser_from_default_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> BrowserTool:
    from chimera.tools.builtin import default_registry

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()
    tool = default_registry(tmp_path, host_exec_confirm=None).get("browser")
    assert isinstance(tool, BrowserTool)
    return tool


def test_the_registry_builds_the_harness_half_only_under_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("playwright")  # the registry holds the browser only where Playwright is
    monkeypatch.delenv("CHIMERA_BROWSER_SITUATION", raising=False)
    assert _browser_from_default_registry(monkeypatch, tmp_path).situation is None
    monkeypatch.setenv("CHIMERA_BROWSER_SITUATION", "true")
    assert isinstance(_browser_from_default_registry(monkeypatch, tmp_path).situation, BrowserSituation)
