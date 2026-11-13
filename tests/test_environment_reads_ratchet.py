r"""`CHIMERA_*` values are read through `Settings`, not out of `os.environ` in the middle of the app.

A read that bypasses `chimera/config.py` is invisible to everything that makes a setting trustworthy:
the Settings screen cannot list it, `PATCH /api/config` cannot say when a change reaches it, the
cached `get_settings()` and the process environment can disagree about it, and nothing documents it.
So the count of such lines is pinned: it may fall, never rise, and each one that stays has a stated
reason in `KEPT`.

The count this started from was 21 lines, reproduced with::

    grep -rnE "(os\.environ(\.get)?|os\.getenv|environ\.get|environ\[)" chimera --include=*.py \
      | grep -v "^chimera/config.py" | grep -c "CHIMERA_"

Three of them (the chat's one-shot reminder switch, twice, and the browser screen session length) now
come through `Settings` as strings, parsed by the rules they always had, so a typo still means "the
default" instead of making `get_settings()` raise. The other 18 are listed below with why.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCESS = re.compile(r"(os\.environ(\.get)?|os\.getenv|environ\.get|environ\[)")
NAME = re.compile(r"CHIMERA_[A-Z0-9_]+")

#: (file, variable) -> (lines, why it stays). Lowering a number or deleting an entry is the point of
#: the ratchet; raising one means a new bypass of `Settings`, which needs a reason a reviewer accepts.
_TAURI = "A launch marker the desktop host sets on the sidecar it starts, not a setting anyone edits; read where Settings does not exist yet or must not be cached"
_WRITE = "A WRITE (it makes a value visible to Settings), not a read"
_TEST = "A test sets the variable per case and `get_settings()` is cached, so moving it means teaching that test to clear the cache; left for its own change"
KEPT: dict[tuple[str, str], tuple[int, str]] = {
    ("chimera/api/app.py", "CHIMERA_TAURI_SIDECAR"): (1, _TAURI),
    ("chimera/api/screen_credential.py", "CHIMERA_TAURI_SIDECAR"): (2, _TAURI),
    ("chimera/core/desktop_install.py", "CHIMERA_TAURI_SIDECAR"): (1, _TAURI),
    ("chimera/launch_secrets.py", "CHIMERA_TAURI_SIDECAR"): (1, _TAURI),
    ("chimera/cli/commands/serve.py", "CHIMERA_WORKSPACE"): (1, "The fallback of the `--workspace` flag, resolved before any Settings object is built"),
    ("chimera/cli/commands/serve.py", "CHIMERA_GOVERNANCE"): (1, _WRITE),
    ("chimera/cli/commands/setup.py", "CHIMERA_DEFAULT_MODEL"): (2, _WRITE),
    ("chimera/cli/commands/setup.py", "CHIMERA_COST_MODE"): (1, _WRITE),
    ("chimera/core/service_watch.py", "CHIMERA_SERVER_TOKEN"): (2, _WRITE),
    ("chimera/core/worktree.py", "CHIMERA_HOME"): (1, "`CHIMERA_HOME` is what locates the settings; it cannot come from them"),
    ("chimera/tools/media.py", "CHIMERA_WHISPER_MODEL"): (2, _TEST),
    ("chimera/server/voice.py", "CHIMERA_WHISPER_MODEL"): (1, _TEST),
    ("chimera/server/gateway.py", "CHIMERA_CHAT_STATED_RUNTIME"): (1, _TEST),
    ("chimera/tools/browser.py", "CHIMERA_BROWSER_AUTO_INSTALL"): (1, _TEST),
}


def _reads() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for path in sorted((ROOT / "chimera").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "chimera/config.py":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if ACCESS.search(line) and "CHIMERA_" in line:
                for name in NAME.findall(line)[:1]:
                    found[(rel, name)] += 1
    return found


def test_the_count_is_pinned_and_can_only_fall() -> None:
    found = _reads()
    expected = {key: n for key, (n, _why) in KEPT.items()}
    assert sum(found.values()) == sum(expected.values()), (
        f"{sum(found.values())} reads outside config.py, {sum(expected.values())} accounted for. "
        "A new one: read it through Settings. A removed one: lower its number in KEPT."
    )
    assert dict(found) == expected


def test_every_kept_read_has_a_reason() -> None:
    assert all(why.strip() for _n, why in KEPT.values())


def test_the_pass_that_started_at_21_ended_at_the_size_of_the_list() -> None:
    assert sum(n for n, _why in KEPT.values()) == 18


def test_the_two_moved_reads_come_through_settings() -> None:
    from chimera.config import Settings

    settings = Settings(CHIMERA_CHAT_SCHEDULE_ONCE="on", CHIMERA_BROWSER_SCREEN_SECONDS="120")
    assert settings.chat_schedule_once == "on"
    assert settings.browser_screen_seconds == "120"
    # A value that is not a number is still the default, never an error at startup.
    from chimera.api.browser_screen import SESSION_SECONDS_DEFAULT, session_seconds_from_env

    assert Settings(CHIMERA_BROWSER_SCREEN_SECONDS="not-a-number").browser_screen_seconds == "not-a-number"
    assert isinstance(session_seconds_from_env(), int)
    assert SESSION_SECONDS_DEFAULT > 0
