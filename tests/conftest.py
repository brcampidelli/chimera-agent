"""Shared test fixtures.

Tests must be hermetic: a developer's real ``.env`` (with live provider keys) must never leak into
the suite, or "without key" tests would see a key and fail. This autouse fixture disables ``.env``
loading for every test, so only the OS environment (which tests drive via ``monkeypatch``)
determines configuration.

It also clears provider keys out of that environment, which the ``.env`` half alone does not do.
That gap was survivable while Chimera recognised exactly five credentials — each test that needed
"no key" deleted those five by hand, and anything else in the environment was ignored by definition.
It stopped being survivable when the credential gate learned to discover ANY ``<PROVIDER>_API_KEY``:
a maintainer with a Groq key exported in their shell would see tests fail that pass in CI, which is
the worst kind of red because it accuses the wrong change. Search and speech credentials are left
alone — they are not providers of models and no gate consults them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from chimera.config import Settings, get_settings
from chimera.providers.discovery import FIRST_CLASS, NOT_A_MODEL_PROVIDER, provider_from_env_var


def _model_provider_keys() -> list[str]:
    return [
        name
        for name in os.environ
        if name not in NOT_A_MODEL_PROVIDER
        and (name in FIRST_CLASS or provider_from_env_var(name) is not None)
    ]


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    config = dict(Settings.model_config)
    config["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", config)
    for name in _model_provider_keys():
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
