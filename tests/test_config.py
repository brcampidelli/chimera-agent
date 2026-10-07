"""Tests for configuration parsing."""

from __future__ import annotations

import pytest

from chimera.config import Settings, get_settings


def test_cli_config_layers_global_project_and_real_environment(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    base = Path(str(tmp_path))
    user_home = base / "user"
    project_a = base / "project-a"
    project_b = base / "project-b"
    (user_home / ".chimera").mkdir(parents=True)
    project_a.mkdir()
    project_b.mkdir()
    (user_home / ".chimera" / ".env").write_text(
        "OPENAI_API_KEY=fake-global\nCHIMERA_DEFAULT_MODEL=global/model\n", encoding="utf-8"
    )
    (project_a / ".env").write_text("OPENAI_API_KEY=fake-project\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.setenv("CHIMERA_HOME", str(user_home / ".chimera"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CHIMERA_DEFAULT_MODEL", raising=False)

    monkeypatch.chdir(project_a)
    get_settings.cache_clear()
    settings_a = get_settings()
    assert settings_a.openai_api_key == "fake-project"
    assert settings_a.default_model == "global/model"
    assert Settings(_env_file=None).home == user_home / ".chimera"
    monkeypatch.delenv("CHIMERA_HOME", raising=False)

    monkeypatch.chdir(project_b)
    get_settings.cache_clear()
    assert get_settings().openai_api_key == "fake-global"
    # CLI invocations from distinct folders resolve sessions against the same user home.
    assert get_settings().home == settings_a.home

    monkeypatch.setenv("OPENAI_API_KEY", "fake-environment")
    get_settings.cache_clear()
    assert get_settings().openai_api_key == "fake-environment"
    get_settings.cache_clear()


def test_existing_project_home_is_kept(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    base = Path(str(tmp_path))
    user_home = base / "user"
    cwd = base / "project"
    (user_home / ".chimera").mkdir(parents=True)
    (cwd / ".chimera").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.delenv("CHIMERA_HOME", raising=False)
    monkeypatch.chdir(cwd)
    # No CHIMERA_HOME: the default itself must keep the project's existing state directory. (Setting
    # CHIMERA_HOME=.chimera here, as this test first did, passes whatever the default is.)
    assert Settings(_env_file=None).home == Path(".chimera")
    # And a folder without one falls through to the user-global home.
    elsewhere = base / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert Settings(_env_file=None).home == user_home / ".chimera"


def test_fusion_panel_splits_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_FUSION_PANEL", "prov/a, prov/b ,prov/c")
    settings = Settings(_env_file=None)
    assert settings.fusion_panel == ["prov/a", "prov/b", "prov/c"]


def test_auto_fuse_defaults_off_and_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_AUTO_FUSE", raising=False)
    assert Settings(_env_file=None).auto_fuse is False  # cheap by default
    monkeypatch.setenv("CHIMERA_AUTO_FUSE", "on")
    assert Settings(_env_file=None).auto_fuse is True


def test_configured_providers_reflects_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    settings = Settings(_env_file=None)
    assert settings.configured_providers() == ["openai"]
    assert settings.has_any_key() is True


def test_no_keys_means_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.has_any_key() is False


def test_settings_load_where_no_home_directory_can_be_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child process with a stripped environment has no HOME or USERPROFILE, and `Path.home()`
    raises there. Measured on the Windows CI job: every entry point failed before doing anything."""
    from pathlib import Path

    from chimera import config

    def no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(config.Path, "home", staticmethod(no_home))
    assert config.config_env_files()[-1].name == ".env"
    assert len(config.config_env_files()) == 1
    assert config._default_home() == Path(".chimera")
