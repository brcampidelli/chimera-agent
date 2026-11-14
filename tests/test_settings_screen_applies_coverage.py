"""Every setting the Settings screen shows says WHEN a saved change starts to apply.

`APPLIES_WHEN` used to list only the exceptions and let an absent key stand for "the next call". An
absence cannot be told apart from a variable nobody looked at, so the screen could print nothing for
a setting whose answer was "after a relaunch" and nobody would know. The screen's variables are read
out of its own source here, so a row added tomorrow without a decision fails this file rather than
shipping silent.
"""

from __future__ import annotations

import re
from pathlib import Path

from chimera.api.config_api import (
    _EDITABLE_SETTINGS,
    ALLOWED_KEYS,
    APPLIES_WHEN,
    COMMANDS_NOW,
    NEXT_CONVERSATION,
    NEXT_LAUNCH,
    NOW,
    UNVERIFIED,
)

SETTINGS_TSX = Path(__file__).resolve().parents[1] / "apps/desktop/src/components/Settings.tsx"
VALID = {NOW, NEXT_CONVERSATION, NEXT_LAUNCH, COMMANDS_NOW, UNVERIFIED}


def _screen_variables() -> set[str]:
    source = SETTINGS_TSX.read_text(encoding="utf-8")
    found = set(re.findall(r'\benv="(CHIMERA_[A-Z0-9_]+)"', source))
    # The Claude-agent role rows build `env` from a table of ["alias", "CHIMERA_...", "tier"].
    found |= set(re.findall(r'\["\w+",\s*"(CHIMERA_[A-Z0-9_]+)",\s*"\w+"\]', source))
    return found


def test_the_screen_variables_were_really_found() -> None:
    # A regex that quietly stopped matching would make every assertion below vacuous.
    found = _screen_variables()
    assert len(found) > 60, sorted(found)
    assert {"CHIMERA_DEFAULT_MODEL", "CHIMERA_CLAUDE_AGENT_FABLE"} <= found


def test_every_variable_the_screen_shows_has_a_stated_moment() -> None:
    missing = sorted(_screen_variables() - set(APPLIES_WHEN))
    assert missing == [], f"no `APPLIES_WHEN` entry (decide it by reading the consumer): {missing}"


def test_every_stated_moment_is_one_the_screen_knows_how_to_say() -> None:
    bad = {key: value for key, value in APPLIES_WHEN.items() if value not in VALID}
    assert bad == {}


def test_a_moment_declared_for_something_nobody_can_save_is_dead_text() -> None:
    # `CHIMERA_SERVER_TOKEN` is a secret: saved through its own route, not the allowlist of plain settings.
    dead = [k for k in APPLIES_WHEN if k not in _EDITABLE_SETTINGS and k not in ALLOWED_KEYS]
    assert dead == []


def test_the_unverified_ones_are_a_short_named_list() -> None:
    # Meta is zero. The list is pinned so it can fall as each consumer is traced, and never grow
    # unnoticed: adding to it is a decision a reviewer sees, not a default.
    unverified = sorted(k for k, v in APPLIES_WHEN.items() if v == UNVERIFIED)
    assert len(unverified) <= 27, unverified
