"""`known_secrets` must not fail when a variable disappears while it scans the environment.

A turn saving its session redacts with `known_secrets()` while, on another thread, a test's teardown
(or anything else) removes a variable. `os.environ.items()` looks each value up during iteration, so
the removal raised KeyError inside the save and the turn failed — seen on CI as a queued turn dying
with `KeyError: 'CLAUDE_CONFIG_DIR'`.
"""

from __future__ import annotations

import os

import pytest

from chimera.core import redact


def test_a_name_removed_during_the_scan_is_skipped_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-" + "a" * 40
    monkeypatch.setenv("CHIMERA_TEST_GONE_TOKEN", "x" * 30)
    monkeypatch.setenv("CHIMERA_TEST_KEPT_TOKEN", secret)
    real_get = os.environ.get
    removed: list[str] = []

    def get_after_removal(name: str, default: str | None = None) -> str | None:
        if name == "CHIMERA_TEST_GONE_TOKEN" and not removed:
            removed.append(name)
            os.environ.pop(name, None)
        return real_get(name, default)

    monkeypatch.setattr(os.environ, "get", get_after_removal)
    found = redact.known_secrets()
    assert removed, "the scan never reached the variable this test removes"
    assert secret in found
    assert "x" * 30 not in found
