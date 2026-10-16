"""The frozen sidecar must install Chromium where Playwright launch looks for it.

Playwright's transport defaults a frozen process to ``PLAYWRIGHT_BROWSERS_PATH=0``. Our
first-use installer gets its driver environment separately; before this fix that put the download
in the user's cache while the later launch searched the bundle's ``.local-browsers`` directory.
These tests record both environments without starting the driver or a browser process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.tools import browser as browser_mod


def _frozen(monkeypatch: pytest.MonkeyPatch, frozen: bool) -> None:
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)


def test_frozen_launch_sets_user_browser_directory_before_driver_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    browser_dir = tmp_path / "ms-playwright"
    # Own the name first: the code under test sets it with a real assignment, and the suite's
    # environment guard fails a test that leaves a variable it never owned.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "owned")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    monkeypatch.setattr("chimera.core.storage.playwright_dir", lambda: (browser_dir, ""))
    _frozen(monkeypatch, True)
    observed: list[str | None] = []

    class Driver:
        def __init__(self, *, headless: bool) -> None:
            observed.append(os.environ.get("PLAYWRIGHT_BROWSERS_PATH"))

    import chimera.tools.browser_playwright as browser_playwright

    monkeypatch.setattr(browser_playwright, "PlaywrightDriver", Driver)
    browser_mod._new_playwright_driver(headless=True)

    assert observed == [str(browser_dir)]
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_dir)


def test_frozen_install_sets_user_browser_directory_before_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    browser_dir = tmp_path / "ms-playwright"
    # Own the name first: the code under test sets it with a real assignment, and the suite's
    # environment guard fails a test that leaves a variable it never owned.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "owned")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    monkeypatch.setattr("chimera.core.storage.playwright_dir", lambda: (browser_dir, ""))
    _frozen(monkeypatch, True)
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(
        browser_mod,
        "_install_command",
        lambda name: ["driver", "cli.js", "install", name],
    )

    def fake_run(argv: list[str], **kwargs: Any) -> None:
        env = kwargs["env"]
        assert isinstance(env, dict)
        environments.append(env)

    monkeypatch.setattr(subprocess, "run", fake_run)
    browser_mod._install_chromium()

    assert len(environments) == 1
    assert environments[0]["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_dir)


@pytest.mark.parametrize("configured", ["0", "D:/custom/playwright-browsers"])
def test_frozen_process_respects_explicit_browser_directory(
    monkeypatch: pytest.MonkeyPatch, configured: str
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", configured)
    monkeypatch.setattr(
        "chimera.core.storage.playwright_dir",
        lambda: (_ for _ in ()).throw(AssertionError("explicit setting must avoid default lookup")),
    )
    _frozen(monkeypatch, True)

    browser_mod._configure_frozen_browser_path()

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == configured


def test_non_frozen_process_does_not_set_browser_directory(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    # Own the name first: the code under test sets it with a real assignment, and the suite's
    # environment guard fails a test that leaves a variable it never owned.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "owned")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    monkeypatch.setattr(
        "chimera.core.storage.playwright_dir",
        lambda: (_ for _ in ()).throw(AssertionError("venv and container must not configure path")),
    )
    _frozen(monkeypatch, False)

    browser_mod._configure_frozen_browser_path()

    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ


def test_install_and_launch_read_the_same_configured_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    browser_dir = tmp_path / "ms-playwright"
    # Own the name first: the code under test sets it with a real assignment, and the suite's
    # environment guard fails a test that leaves a variable it never owned.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "owned")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    monkeypatch.setattr("chimera.core.storage.playwright_dir", lambda: (browser_dir, ""))
    _frozen(monkeypatch, True)
    environments: list[dict[str, str]] = []

    def environment() -> dict[str, str]:
        result = dict(os.environ)
        environments.append(result)
        return result

    monkeypatch.setattr(browser_mod, "_driver_env", environment)
    monkeypatch.setattr(
        browser_mod,
        "_install_command",
        lambda name: ["driver", "cli.js", "install", name],
    )
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: None)
    browser_mod._install_chromium()

    class Driver:
        def __init__(self, *, headless: bool) -> None:
            environment()

    import chimera.tools.browser_playwright as browser_playwright

    monkeypatch.setattr(browser_playwright, "PlaywrightDriver", Driver)
    browser_mod._new_playwright_driver(headless=True)

    assert len(environments) == 2
    assert environments[0]["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_dir)
    assert environments[1]["PLAYWRIGHT_BROWSERS_PATH"] == environments[0]["PLAYWRIGHT_BROWSERS_PATH"]
