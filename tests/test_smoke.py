"""Smoke tests: the package imports and the basic seams work without any key."""

from __future__ import annotations

import chimera
from chimera.config import Settings
from chimera.telemetry import get_logger


def test_version_is_exposed() -> None:
    assert isinstance(chimera.__version__, str)
    assert chimera.__version__


def test_settings_construct_without_env() -> None:
    settings = Settings(_env_file=None)
    assert settings.default_model
    assert settings.fusion_panel
    assert isinstance(settings.fusion_panel, list)


def test_logger_is_namespaced() -> None:
    log = get_logger("tests")
    assert log.name == "chimera.tests"
