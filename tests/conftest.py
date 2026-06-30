"""Shared test fixtures.

Tests must be hermetic: a developer's real ``.env`` (with live provider keys)
must never leak into the suite, or "without key" tests would see a key and fail.
This autouse fixture disables ``.env`` loading for every test, so only the OS
environment (which tests drive via ``monkeypatch``) determines configuration.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from chimera.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    config = dict(Settings.model_config)
    config["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", config)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
