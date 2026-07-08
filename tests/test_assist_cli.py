"""Tests for `chimera assist` + the models/profile CLI surface (no network calls)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os

    tier_vars = ("CHIMERA_WEAK_MODEL", "CHIMERA_MID_MODEL", "CHIMERA_ORCHESTRATOR_MODEL",
                 "CHIMERA_COST_MODE", "CHIMERA_CASCADE")
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    for var in tier_vars:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    # `models set` writes os.environ directly (outside monkeypatch) — scrub it so
    # a pinned tier/mode can never leak into other test files.
    for var in tier_vars:
        os.environ.pop(var, None)
    get_settings.cache_clear()


def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_assist_without_key_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    get_settings.cache_clear()
    result = runner.invoke(app, ["assist"])
    assert result.exit_code == 1
    assert "No provider key" in result.stdout


def test_assist_exit_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    result = runner.invoke(app, ["assist"], input="/exit\n")
    assert result.exit_code == 0
    assert "cheap by default" in result.stdout
    assert "tiers:" in result.stdout  # the ladder banner


def test_assist_profile_slash_command_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.interface.profile import load_profile, profile_path

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    result = runner.invoke(
        app, ["assist"], input="/profile preference: answer in PT-BR\n/exit\n"
    )
    assert result.exit_code == 0
    assert "stored" in result.stdout
    stored = load_profile(profile_path(get_settings().home))
    assert "answer in PT-BR" in stored.preferences


def test_assist_profile_slash_command_usage_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    result = runner.invoke(app, ["assist"], input="/profile nonsense\n/exit\n")
    assert "usage: /profile" in result.stdout


def test_profile_cli_set_show_forget() -> None:
    result = runner.invoke(app, ["profile", "set", "preference", "dark mode"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["profile", "show"])
    assert result.exit_code == 0
    assert "dark mode" in result.stdout
    result = runner.invoke(app, ["profile", "forget", "dark mode"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["profile", "forget", "dark mode"])
    assert result.exit_code == 1  # already gone


def test_profile_cli_show_empty() -> None:
    result = runner.invoke(app, ["profile", "show"])
    assert result.exit_code == 0
    assert "No profile yet" in result.stdout


def test_models_shows_tier_ladder() -> None:
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    assert "tier: weak" in result.stdout
    assert "tier: top (orchestrator)" in result.stdout
    assert "cost_mode=auto" in result.stdout


def test_models_catalog_filters_by_tier() -> None:
    result = runner.invoke(app, ["models", "catalog", "--tier", "top"])
    assert result.exit_code == 0
    assert "DeepSeek" in result.stdout
    result = runner.invoke(app, ["models", "catalog", "--tier", "bogus"])
    assert result.exit_code == 1


def test_models_set_and_unpin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # .env writes land in the tmp cwd
    result = runner.invoke(app, ["models", "set", "mid", "openrouter/z-ai/glm-4.6"])
    assert result.exit_code == 0
    assert get_settings().tier_ladder().mid == "openrouter/z-ai/glm-4.6"
    result = runner.invoke(app, ["models", "set", "mid", "auto"])
    assert result.exit_code == 0
    assert get_settings().tier_ladder().mid != "openrouter/z-ai/glm-4.6"
    result = runner.invoke(app, ["models", "set", "mode", "premium"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["models", "set", "mode", "warp-speed"])
    assert result.exit_code == 1
    result = runner.invoke(app, ["models", "set", "bogus-role", "x"])
    assert result.exit_code == 1
