"""The desktop app (`chimera app`) fires scheduled jobs while it is open.

The reactive gateway alone never runs the clock, so before this a "briefing at 7am" needed a
separate `chimera serve --cron` terminal running 24/7 — proactivity was impossible from the desktop
app. These tests pin that the `app` command starts (and stops) the same cron daemon the serve path
uses, honours the CHIMERA_APP_CRON setting and the --no-cron flag, and never starts it keyless.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from typer.testing import CliRunner

pytest.importorskip("fastapi")  # the `app` command needs the desktop extra

from chimera.cli.main import app  # noqa: E402

runner = CliRunner()


class _Spy:
    """Records how `_start_cron_daemon` was called and hands back a stop event we can inspect."""

    def __init__(self) -> None:
        self.calls = 0
        self.stop = threading.Event()

    def __call__(self, *args: Any, **kwargs: Any) -> threading.Event:
        self.calls += 1
        return self.stop


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    """Neutralise the blocking/IO parts of the command and spy on the daemon start."""
    spy = _Spy()
    monkeypatch.setattr("chimera.cli.main._start_cron_daemon", spy)
    # uvicorn is imported inside the command; patch Server.run so it returns instead of serving.
    monkeypatch.setattr("uvicorn.Server.run", lambda self, **kw: None)
    return spy


def test_app_starts_the_cron_daemon_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")  # a key must be present to run jobs
    spy = _patch_runtime(monkeypatch)

    result = runner.invoke(app, ["app", "--no-open", "--no-memory", "--port", "0"])

    assert result.exit_code == 0, result.output
    assert spy.calls == 1  # the daemon was started
    assert spy.stop.is_set()  # ...and stopped when the server returned (the finally block)


def test_no_cron_flag_keeps_the_app_reactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    spy = _patch_runtime(monkeypatch)

    result = runner.invoke(app, ["app", "--no-open", "--no-memory", "--no-cron", "--port", "0"])

    assert result.exit_code == 0, result.output
    assert spy.calls == 0  # explicitly reactive: no daemon


def test_app_cron_setting_off_keeps_the_app_reactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("CHIMERA_APP_CRON", "0")  # the setting, not the flag
    spy = _patch_runtime(monkeypatch)

    result = runner.invoke(app, ["app", "--no-open", "--no-memory", "--port", "0"])

    assert result.exit_code == 0, result.output
    assert spy.calls == 0


def test_keyless_app_does_not_start_the_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    # A keyless boot opens the setup screen and has no backend to run jobs — the daemon must wait.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    spy = _patch_runtime(monkeypatch)

    result = runner.invoke(app, ["app", "--no-open", "--no-memory", "--port", "0"])

    assert result.exit_code == 0, result.output
    assert spy.calls == 0
