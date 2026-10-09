"""Study 30's opt-in governance rules and the wire log have a row on the Settings screen.

Before this, four of them could be turned on only by editing `.env`, and the two that `PATCH
/config` already accepted were not reported by `GET /config`, so a screen could not show their
state. These tests hold the wiring the "Governance and audit" card depends on: each one ships off,
the screen reads it in one block, a value the app could not start on is refused before it is
written, clearing the deadline leaves an app that starts, each says when a save applies, and the
desktop bridge may write none of them. What each rule DOES is held by its own tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.bridge_routes import GUARD_SETTINGS, OWNER_ONLY_SETTINGS, SUGGESTABLE_SETTINGS
from chimera.api.config_api import (
    APPLIES_WHEN,
    NEXT_CONVERSATION,
    is_editable,
    patch_config,
    read_config,
)
from chimera.api.schemas import ConfigOut
from chimera.config import Settings

SWITCHES = {
    "CHIMERA_WIRE_LOG": "wire_log",
    "CHIMERA_EXFIL_HOST_PATH": "exfil_host_path",
    "CHIMERA_SHELL_FETCH_GUARD": "shell_fetch_guard",
    "CHIMERA_TAINT_ROPE_LITE": "taint_rope_lite",
    "CHIMERA_ARM_ON_RECALLED_LESSONS": "arm_on_recalled_lessons",
}
DEADLINE = "CHIMERA_GOVERNANCE_BAND_DEADLINE_S"


def _settings(tmp_path: Path, **env: str) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path), **env)  # type: ignore[call-arg]


def test_every_one_ships_off_and_the_screen_reads_them_in_one_block(tmp_path: Path) -> None:
    block = read_config(_settings(tmp_path))["governance_audit"]

    assert block == {
        "wire_log": False,
        "exfil_host_path": False,
        "shell_fetch_guard": False,
        "taint_rope_lite": False,
        "arm_on_recalled_lessons": False,
        "band_deadline_s": None,
        "band_on": False,
    }


@pytest.mark.parametrize(("key", "field"), sorted(SWITCHES.items()))
def test_each_switch_is_editable_and_read_back(tmp_path: Path, key: str, field: str) -> None:
    assert is_editable(key)
    on = read_config(_settings(tmp_path, **{key: "true"}))["governance_audit"]

    assert on[field] is True
    assert [name for name, value in on.items() if value is True] == [field]


def test_the_deadline_is_read_back_beside_whether_the_band_runs(tmp_path: Path) -> None:
    assert is_editable(DEADLINE)
    block = read_config(
        _settings(tmp_path, CHIMERA_GOVERNANCE_BAND_DEADLINE_S="4.5")
    )["governance_audit"]
    assert block["band_deadline_s"] == 4.5
    # A deadline with the band off does nothing, and the block says the band is off.
    assert block["band_on"] is False

    running = read_config(
        _settings(tmp_path, CHIMERA_GOVERNANCE="enforce", CHIMERA_GOVERNANCE_BAND="on")
    )["governance_audit"]
    assert running["band_on"] is True
    # `on` without a mode that judges is not a running band — the band's own predicate decides.
    idle = read_config(_settings(tmp_path, CHIMERA_GOVERNANCE_BAND="on"))["governance_audit"]
    assert idle["band_on"] is False


def test_the_block_matches_the_response_schema(tmp_path: Path) -> None:
    out = ConfigOut.model_validate(read_config(_settings(tmp_path, CHIMERA_WIRE_LOG="true")))
    assert out.governance_audit.wire_log is True
    # A server that predates the block reads as all off, which is what it does.
    assert ConfigOut.model_fields["governance_audit"].default_factory is not None


@pytest.mark.parametrize("key", sorted(SWITCHES))
@pytest.mark.parametrize("value", ["maybe", "2", "enabled"])
def test_a_switch_the_app_could_not_start_on_is_refused(tmp_path: Path, key: str, value: str) -> None:
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match=key):
        patch_config({key: value}, env_path=env)
    assert not env.exists() or key not in env.read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["0", "-3", "soon", "inf", "nan"])
def test_a_deadline_the_app_could_not_start_on_is_refused(tmp_path: Path, value: str) -> None:
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match=DEADLINE):
        patch_config({DEADLINE: value}, env_path=env)
    assert not env.exists() or DEADLINE not in env.read_text(encoding="utf-8")


def test_clearing_the_deadline_leaves_an_app_that_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The card clears the field by saving it empty. Before the validator, pydantic read `""` as a
    float that failed to parse, and every later read of the settings raised."""
    env = tmp_path / ".env"
    # Owned before the save, which exports what it writes to this process's environment.
    monkeypatch.setenv(DEADLINE, "5")
    patch_config({DEADLINE: ""}, env_path=env)

    assert f"{DEADLINE}=" in env.read_text(encoding="utf-8").splitlines()
    assert _settings(tmp_path, CHIMERA_GOVERNANCE_BAND_DEADLINE_S="").governance_band_deadline_s is None
    assert _settings(tmp_path, CHIMERA_GOVERNANCE_BAND_DEADLINE_S="  ").governance_band_deadline_s is None


def test_each_says_when_a_save_applies() -> None:
    """The ledger and the band are built once per conversation; the gateway and the autonomous run
    read theirs per call and per run, so those two have no entry (the next call)."""
    for key in ("CHIMERA_EXFIL_HOST_PATH", "CHIMERA_SHELL_FETCH_GUARD", "CHIMERA_TAINT_ROPE_LITE", DEADLINE):
        assert APPLIES_WHEN[key] == NEXT_CONVERSATION, key
    assert "CHIMERA_WIRE_LOG" not in APPLIES_WHEN
    assert "CHIMERA_ARM_ON_RECALLED_LESSONS" not in APPLIES_WHEN


@pytest.mark.parametrize("key", [*sorted(SWITCHES), DEADLINE])
def test_only_the_owner_may_change_it(key: str) -> None:
    """Each only adds a question, arms a run or keeps a record — so its other direction loosens.
    Refused flat to the bridge, never merely suggestable."""
    assert key in GUARD_SETTINGS
    assert key in OWNER_ONLY_SETTINGS
    assert key not in SUGGESTABLE_SETTINGS
