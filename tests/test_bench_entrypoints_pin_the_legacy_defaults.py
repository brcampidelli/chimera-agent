"""Every bench entry point pins the five switches whose product default changed on 2026-11-10.

A bench's numbers were measured with those switches off. Rerunning the bench with the new defaults
measures a different system, so each runnable script in `bench/` calls `pin_legacy_defaults()` inside
its `__main__` guard, and the shared launcher (`bench/_with_env.py`) pins before it runs anything.

`pin_legacy_defaults` uses `setdefault`: an arm that sets a switch itself keeps its value. The sweep
below therefore accepts a script that pins, or that names every switch itself (an arm that fixes the
values explicitly, so the pin would not change anything it does).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench"
GUARD = re.compile(r'^\s*if __name__ == ["\']__main__["\']:\s*$', re.MULTILINE)
PIN_CALL = "pin_legacy_defaults()"
SWITCHES = (
    "CHIMERA_DEFER_TOOLS",
    "CHIMERA_SKILL_CARDS",
    "CHIMERA_CODE_CLOSE_LOOP",
    "CHIMERA_PULL_REQUESTS",
    "CHIMERA_CHAT_INBOUND_MEDIA",
)
#: The helper and the fixture that is a frozen, hash-checked copy of a tampered file (it must not change).
EXEMPT = {"_legacy_defaults.py", "test_slugify.py"}


def _entry_points() -> list[Path]:
    out = []
    for path in sorted(BENCH.rglob("*.py")):
        if "__pycache__" in path.parts or path.name in EXEMPT:
            continue
        if GUARD.search(path.read_text(encoding="utf-8")):
            out.append(path)
    return out


def test_the_sweep_finds_the_entry_points() -> None:
    entries = _entry_points()
    assert len(entries) > 200, "the sweep should see every runnable bench script"


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: str(p.relative_to(BENCH)))
def test_every_entry_point_pins_or_fixes_the_switches(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    guard = GUARD.search(text)
    assert guard is not None
    after_guard = text[guard.end():]
    fixes_all = all(name in text for name in SWITCHES)
    assert PIN_CALL in after_guard or fixes_all, (
        f"{path.relative_to(BENCH)} runs without pinning the 2026-11-10 defaults; its numbers would "
        "be measured with the new ones"
    )


def test_the_shared_launcher_pins_before_it_runs_a_target() -> None:
    text = (BENCH / "_with_env.py").read_text(encoding="utf-8")
    assert PIN_CALL in text
    assert text.index(PIN_CALL) < text.index("runpy.run_path"), "the pin must come before the target runs"


def test_pin_fills_only_what_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    sys.path.insert(0, str(BENCH))
    try:
        from _legacy_defaults import LEGACY_VALUES, pin_legacy_defaults
    finally:
        sys.path.pop(0)

    for name in SWITCHES:
        monkeypatch.setenv(name, "sentinel")  # registers the variable so monkeypatch restores it
        monkeypatch.delenv(name)
    monkeypatch.setenv("CHIMERA_PULL_REQUESTS", "1")  # an arm's own value must survive the pin

    pin_legacy_defaults()

    import os

    assert os.environ["CHIMERA_PULL_REQUESTS"] == "1"
    for name, value in LEGACY_VALUES.items():
        if name != "CHIMERA_PULL_REQUESTS":
            assert os.environ[name] == value
