"""The permission modes: the default each Settings approval reads as, the table behind every mode, the
rule a paired device is held to, and the mode a conversation keeps.

The fixture ``tests/fixtures/permission_modes.json`` is the table both sides must give. The Code screen
(``apps/desktop/src/lib/permission-modes.ts``) reads the same file in its own test, so the two cannot
drift apart without one of the suites failing.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.api.posture import (
    ALL_MODES,
    STORABLE_MODES,
    Posture,
    bridge_mode,
    bridge_turn,
    mode_for_approval,
    mode_turn,
    narrow_posture,
    widens,
)
from chimera.core.code_session import CodeSession, CodeSessionStore

FIXTURE = Path(__file__).parent / "fixtures" / "permission_modes.json"


# --- The default each Settings approval reads as ----------------------------------------------------


def test_settings_always_reads_as_manual() -> None:
    assert mode_for_approval("always") == "manual"


def test_settings_suspicious_reads_as_auto() -> None:
    assert mode_for_approval("suspicious") == "auto"


def test_settings_never_reads_as_dont_ask_and_keeps_its_posture() -> None:
    # Not Accept edits: a configured `never` keeps the posture it always had (approval never, commands
    # as granted), so nobody's behaviour moves when the selector appears.
    assert mode_for_approval("never") == "dont_ask"
    assert mode_turn("dont_ask", "workspace_shell", "never").posture == Posture(
        reach="workspace_shell", approval="never", commands="granted"
    )


# --- The table, against the fixture ------------------------------------------------------------------


def _fixture_rows() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]


@pytest.mark.parametrize("row", _fixture_rows(), ids=lambda r: f"{r['mode']}-{r['base']['reach']}-{r['base']['approval']}")
def test_every_mode_gives_the_fixture_posture(row: dict[str, Any]) -> None:
    turn = mode_turn(row["mode"], row["base"]["reach"], row["base"]["approval"])
    assert turn.posture.model_dump() == row["posture"]
    assert turn.plan_gate is row["planGate"]


def test_only_plan_forces_the_plan_gate() -> None:
    for row in _fixture_rows():
        assert row["planGate"] is (row["mode"] == "plan")


def test_accept_edits_never_raises_a_read_only_owner() -> None:
    assert mode_turn("accept_edits", "read_only", "always").posture.reach == "read_only"


# --- Bypass is never stored, and never chosen by a paired device ------------------------------------


def test_bypass_is_not_a_storable_mode() -> None:
    assert "bypass" not in STORABLE_MODES
    assert set(STORABLE_MODES) == set(ALL_MODES) - {"bypass"}


def test_a_paired_device_never_gets_bypass() -> None:
    assert bridge_mode("bypass", "manual") == "manual"
    assert bridge_mode("bypass", "dont_ask") == "dont_ask"


def test_a_paired_device_may_choose_its_own_mode_or_a_narrower_one() -> None:
    assert bridge_mode("dont_ask", "dont_ask") == "dont_ask"  # equal: allowed
    assert bridge_mode("manual", "dont_ask") == "manual"  # narrower: allowed
    assert bridge_mode("plan", "auto") == "plan"  # narrowest: allowed


def test_a_paired_device_cannot_widen_the_conversation() -> None:
    assert bridge_mode("dont_ask", "manual") == "manual"
    assert bridge_mode("auto", "plan") == "plan"


def test_a_paired_device_without_a_choice_gets_the_conversations_mode() -> None:
    assert bridge_mode(None, "accept_edits") == "accept_edits"
    assert bridge_mode("made-up-mode", "accept_edits") == "accept_edits"


def test_a_paired_device_turn_runs_under_the_chosen_posture_not_its_own() -> None:
    # The bridge asks for the widest posture it can name; the conversation is in Manual, so it gets
    # Manual's posture (approval always), and nothing it sent survives.
    mode, turn = bridge_turn("dont_ask", "manual", "workspace", "suspicious")
    assert mode == "manual"
    assert turn.posture == Posture(reach="workspace", approval="always", commands="granted")
    assert turn.plan_gate is False


def test_a_paired_device_narrowed_to_plan_has_the_gate_forced() -> None:
    mode, turn = bridge_turn("plan", "dont_ask", "workspace", "never")
    assert mode == "plan"
    assert turn.plan_gate is True


def test_a_sent_posture_can_only_narrow_the_modes() -> None:
    # The device sends {always, asks}; the conversation is in Auto ({suspicious, granted}). Replacing
    # the sent posture by the mode's widened approval and commands. Axis by axis, the device's
    # narrower answers stay.
    sent = Posture(reach="workspace", approval="always", commands="asks")
    mode, turn = bridge_turn(None, "auto", "workspace", "suspicious", sent)
    assert mode == "auto"
    assert turn.posture == Posture(reach="workspace", approval="always", commands="asks")
    wide = Posture(reach="workspace_shell", approval="never", commands="granted")
    _, turn = bridge_turn(None, "manual", "workspace", "suspicious", wide)
    assert turn.posture == Posture(reach="workspace", approval="always", commands="granted")


def test_narrow_posture_is_the_more_restrictive_axis_by_axis() -> None:
    a = Posture(reach="read_only", approval="never", commands="granted")
    b = Posture(reach="workspace_shell", approval="always", commands="asks")
    assert narrow_posture(a, b) == Posture(reach="read_only", approval="always", commands="asks")
    assert narrow_posture(b, a) == narrow_posture(a, b)


def test_no_sent_posture_uses_the_configured_reach_not_a_hardcoded_one() -> None:
    _, turn = bridge_turn(None, "dont_ask", "read_only", "suspicious")
    assert turn.posture.reach == "read_only"


def _turn(mode: str, reach: str = "workspace", approval: str = "suspicious") -> Any:
    return mode_turn(mode, reach, approval)  # type: ignore[arg-type]


def test_accept_edits_is_not_narrower_than_auto_because_it_raises_the_reach() -> None:
    # A single rank put Accept edits below Auto. It mounts the shell (reach workspace_shell) that Auto
    # over a `workspace` reach does not, so a device in Auto must not be able to choose it.
    assert widens(_turn("accept_edits"), _turn("auto")) is True
    assert bridge_mode("accept_edits", "auto", "workspace", "suspicious") == "auto"


def test_the_widening_matrix_axis_by_axis() -> None:
    reaches = ["read_only", "workspace", "workspace_shell"]
    approvals = ["always", "suspicious", "never"]
    for requested in STORABLE_MODES:
        for current in STORABLE_MODES:
            c, r = _turn(requested), _turn(current)
            expected = (
                reaches.index(c.posture.reach) > reaches.index(r.posture.reach)
                or approvals.index(c.posture.approval) > approvals.index(r.posture.approval)
                or (c.posture.commands == "granted" and r.posture.commands == "asks")
                or (r.plan_gate and not c.plan_gate)
            )
            assert widens(c, r) is expected, (requested, current)
            got = bridge_mode(requested, current, "workspace", "suspicious")  # type: ignore[arg-type]
            assert got == (current if expected else requested), (requested, current)


def test_no_mode_widens_itself_and_bypass_is_never_chosen() -> None:
    for mode in STORABLE_MODES:
        assert widens(_turn(mode), _turn(mode)) is False
        assert bridge_mode("bypass", mode, "workspace", "suspicious") == mode  # type: ignore[arg-type]


# --- The conversation keeps its mode -----------------------------------------------------------------


def test_a_stored_mode_round_trips_and_survives_a_turn_save(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path)
    store.set_stored_permission_mode("sess-1", "dont_ask")
    assert store.stored_permission_mode("sess-1") == "dont_ask"

    # A turn saves the whole conversation; the owner's stored mode must come through it unchanged.
    session = CodeSession(SimpleNamespace(), session_id="sess-1")
    store.save(session)
    assert store.stored_permission_mode("sess-1") == "dont_ask"


def test_a_conversation_with_no_stored_mode_reads_empty(tmp_path: Path) -> None:
    assert CodeSessionStore(tmp_path).stored_permission_mode("never-chosen") == ""


def test_plan_is_never_wider_than_auto_on_approval() -> None:
    assert mode_turn("plan", "workspace", "never").posture.approval == "suspicious"
    assert mode_turn("plan", "workspace", "always").posture.approval == "always"


def test_a_bridge_can_descend_from_auto_to_plan_with_never_in_settings() -> None:
    # Settings approval `never`, conversation stored in Auto. Plan used to carry `never`, wider than
    # Auto, and the descent was refused as a widening.
    assert bridge_mode("plan", "auto", "workspace", "never") == "plan"
    mode, turn = bridge_turn("plan", "auto", "workspace", "never")
    assert mode == "plan" and turn.plan_gate is True
    assert turn.posture.approval == "suspicious"
