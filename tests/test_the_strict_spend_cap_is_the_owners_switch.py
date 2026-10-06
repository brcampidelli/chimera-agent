"""The strict spend cap is a switch on the Settings screen, off as shipped, that only the owner flips.

The owner's decision of 2026-10-05: whether a typed dollar ceiling may be passed by one call (the
reservation cap, the shipped behaviour) or never (strict) is his choice, made on a named row. These
tests hold the wiring: the screen may write and read it, a value the app could not start on is
refused before it is saved, it applies from the next run without a relaunch, and the desktop bridge
may not write it, since switching it off loosens a limit. What the switch DOES is held in
``test_a_strict_spend_cap_never_passes_the_ceiling.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.bridge_routes import GUARD_SETTINGS, OWNER_ONLY_SETTINGS, SUGGESTABLE_SETTINGS
from chimera.api.config_api import APPLIES_WHEN, is_editable, patch_config, read_config
from chimera.config import Settings, get_settings
from chimera.orchestration.budget import SpendBudget


def test_it_ships_off() -> None:
    assert Settings().strict_spend_cap is False  # type: ignore[call-arg]


def test_the_screen_may_write_and_read_it(tmp_path: Path) -> None:
    assert is_editable("CHIMERA_STRICT_SPEND_CAP")
    strict = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_STRICT_SPEND_CAP="true")  # type: ignore[call-arg]

    assert read_config(strict)["spend"]["strict_cap"] is True
    assert read_config(Settings(CHIMERA_HOME=str(tmp_path)))["spend"]["strict_cap"] is False  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["maybe", "2", "strict"])
def test_a_value_the_app_could_not_start_on_is_refused(tmp_path: Path, value: str) -> None:
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match="CHIMERA_STRICT_SPEND_CAP"):
        patch_config({"CHIMERA_STRICT_SPEND_CAP": value}, env_path=env)
    assert not env.exists() or "CHIMERA_STRICT_SPEND_CAP" not in env.read_text(encoding="utf-8")


def test_saved_from_the_screen_the_next_run_is_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read when each run builds its budget, so no APPLIES_WHEN entry and no relaunch."""
    assert "CHIMERA_STRICT_SPEND_CAP" not in APPLIES_WHEN
    env = tmp_path / ".env"
    monkeypatch.chdir(tmp_path)
    # Owned before the save, which exports what it writes to this process's environment.
    monkeypatch.setenv("CHIMERA_STRICT_SPEND_CAP", "")
    monkeypatch.delenv("CHIMERA_STRICT_SPEND_CAP")

    patch_config({"CHIMERA_STRICT_SPEND_CAP": "true"}, env_path=env)
    get_settings.cache_clear()

    assert "CHIMERA_STRICT_SPEND_CAP=true" in env.read_text(encoding="utf-8")
    assert SpendBudget(max_usd=1.0).strict is True


def test_only_the_owner_may_switch_it() -> None:
    """It changes a limit: off lets a run pass a ceiling the owner chose to hold hard. Refused flat
    to the bridge, like the daily cap beside it, and never merely suggestable."""
    assert "CHIMERA_STRICT_SPEND_CAP" in GUARD_SETTINGS
    assert "CHIMERA_STRICT_SPEND_CAP" in OWNER_ONLY_SETTINGS
    assert "CHIMERA_STRICT_SPEND_CAP" not in SUGGESTABLE_SETTINGS
