"""Tests for the CLI surface (no network calls)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from chimera import __version__
from chimera.cli.main import app
from chimera.config import get_settings

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_models_command_lists_panel() -> None:
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    assert "fusion panel" in result.stdout


def test_doctor_ready_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Ready" in result.stdout
    finally:
        get_settings.cache_clear()
