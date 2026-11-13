"""Every change of "Ignore permissions" lands on the audit chain, and an expiry is honoured by the clock.

Before this, `bypass_switches.py` imported no audit at all: the chain recorded what a switch
RELEASED and never who turned the questions off, or when (audit 2026-11, a07-08). These tests read
the same `audit.jsonl` the kernel writes, through `AuditLog`, so a second log would fail them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.core.bypass_switches import (
    ORIGIN_REACTIVATE,
    ORIGIN_SCREEN,
    ORIGIN_TTL,
    SWITCH_FILE_NAME,
    TOGGLE_EVENT,
    BypassSwitches,
)
from chimera.governance.audit import AuditLog


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _toggles(home: Path) -> list[dict[str, object]]:
    return [e for e in AuditLog(home / "audit.jsonl").entries() if e.get("type") == TOGGLE_EVENT]


def test_on_and_off_write_exactly_two_events_with_scope_and_origin(tmp_path: Path) -> None:
    switches = BypassSwitches(tmp_path / SWITCH_FILE_NAME)
    switches.set_conversation("c1", True)
    switches.set_conversation("c1", False)

    events = _toggles(tmp_path)
    assert len(events) == 2
    assert [(e["scope"], e["id"], e["enabled"], e["origin"]) for e in events] == [
        ("conversation", "c1", True, ORIGIN_SCREEN),
        ("conversation", "c1", False, ORIGIN_SCREEN),
    ]
    assert AuditLog(tmp_path / "audit.jsonl").verify().ok


def test_a_project_switch_is_recorded_with_its_scope(tmp_path: Path) -> None:
    switches = BypassSwitches(tmp_path / SWITCH_FILE_NAME)
    switches.set_project("/repo/shop", True)
    switches.set_project("/repo/shop", False)
    assert [(e["scope"], e["enabled"]) for e in _toggles(tmp_path)] == [("project", True), ("project", False)]


def test_a_ttl_of_sixty_seconds_holds_at_59_and_is_off_at_61(tmp_path: Path) -> None:
    clock = Clock()
    switches = BypassSwitches(tmp_path / SWITCH_FILE_NAME, clock=clock)
    switches.set_conversation("c1", True, ttl_seconds=60)

    clock.now += 59
    assert switches.conversation_on("c1") is True
    clock.now += 2
    assert switches.conversation_on("c1") is False

    events = _toggles(tmp_path)
    assert len(events) == 2, "on, then the expiry: nothing more"
    assert events[0]["enabled"] is True and events[0]["expires_at"] == pytest.approx(1_060.0)
    assert (events[1]["enabled"], events[1]["origin"]) == (False, ORIGIN_TTL)
    # And a third, separate event when the owner then turns it off: three in all.
    switches.set_conversation("c1", False)
    assert [e["origin"] for e in _toggles(tmp_path)] == [ORIGIN_SCREEN, ORIGIN_TTL, ORIGIN_SCREEN]


def test_the_expiry_is_checked_by_every_reader(tmp_path: Path) -> None:
    clock = Clock()
    switches = BypassSwitches(tmp_path / SWITCH_FILE_NAME, clock=clock)
    switches.set_project("/repo/a", True, ttl_seconds=10)
    clock.now += 10
    assert switches.any_project_on() is False
    assert switches.live_entries() == []
    on_disk = json.loads((tmp_path / SWITCH_FILE_NAME).read_text(encoding="utf-8"))
    assert on_disk["projects"] == []


def test_no_ttl_is_the_default_and_never_expires(tmp_path: Path) -> None:
    clock = Clock()
    switches = BypassSwitches(tmp_path / SWITCH_FILE_NAME, clock=clock)
    switches.set_project("/repo/a", True)
    clock.now += 10 * 365 * 24 * 3600
    assert switches.project_on("/repo/a") is True
    on_disk = json.loads((tmp_path / SWITCH_FILE_NAME).read_text(encoding="utf-8"))
    assert "expires" not in on_disk, "a file with no expiry is what it always was"


def test_only_the_owners_screen_turns_it_on(tmp_path: Path) -> None:
    switches = BypassSwitches(tmp_path / SWITCH_FILE_NAME)
    with pytest.raises(PermissionError):
        switches.set_conversation("c1", True, origin="bridge")
    with pytest.raises(PermissionError):
        switches.set_project("/repo/a", True, origin=ORIGIN_TTL)
    assert switches.conversation_on("c1") is False
    assert _toggles(tmp_path) == [], "a refused ON writes no ON event"


def test_reactivation_is_recorded_and_an_expired_saved_entry_stays_off(tmp_path: Path) -> None:
    path = tmp_path / SWITCH_FILE_NAME
    path.write_text(
        json.dumps({
            "conversations": ["fresh", "stale"],
            "projects": [],
            "expires": {"conversation:stale": 500.0, "conversation:fresh": 5_000.0},
        }),
        encoding="utf-8",
    )
    switches = BypassSwitches(path, clock=Clock(1_000.0))
    assert switches.reactivate([], ["fresh", "stale"]) == (0, 1)
    assert switches.conversation_on("fresh") and not switches.conversation_on("stale")
    events = _toggles(tmp_path)
    assert [(e["id"], e["origin"]) for e in events] == [("fresh", ORIGIN_REACTIVATE)]
    assert switches.live_entries()[0].expires_at == pytest.approx(5_000.0)


def test_deleting_a_conversation_records_the_switch_going_off(tmp_path: Path) -> None:
    switches = BypassSwitches(tmp_path / SWITCH_FILE_NAME)
    switches.set_conversation("c1", True)
    switches.forget_conversation("c1")
    switches.forget_conversation("never-on")
    assert [(e["id"], e["enabled"], e["origin"]) for e in _toggles(tmp_path)] == [
        ("c1", True, ORIGIN_SCREEN),
        ("c1", False, "deleted"),
    ]


def test_a_failed_save_of_an_on_records_nothing(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    switches = BypassSwitches(blocker / SWITCH_FILE_NAME, audit_path=tmp_path / "audit.jsonl")
    with pytest.raises(OSError):
        switches.set_conversation("c1", True)
    assert _toggles(tmp_path) == []
