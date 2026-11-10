"""Pin the switches whose product default changed on 2026-11-10 to the value the bench measured with.

The owner turned five features on by default (`CHIMERA_DEFER_TOOLS`, `CHIMERA_SKILL_CARDS`,
`CHIMERA_CODE_CLOSE_LOOP`, `CHIMERA_PULL_REQUESTS`, `CHIMERA_CHAT_INBOUND_MEDIA`). Every bench here
was measured with them off, so a rerun must keep them off or it measures a different system.

`setdefault` on purpose: an arm that sets a value keeps it, and a value already in the environment
wins. Only what is unset gets the old value.
"""

from __future__ import annotations

import os

LEGACY_VALUES: dict[str, str] = {
    "CHIMERA_DEFER_TOOLS": "0",
    "CHIMERA_SKILL_CARDS": "0",
    "CHIMERA_CODE_CLOSE_LOOP": "0",
    "CHIMERA_PULL_REQUESTS": "0",
    "CHIMERA_CHAT_INBOUND_MEDIA": "0",
}


def pin_legacy_defaults() -> None:
    """Fill in the pre-2026-11-10 value for each switch the environment leaves unset."""
    for name, value in LEGACY_VALUES.items():
        os.environ.setdefault(name, value)
