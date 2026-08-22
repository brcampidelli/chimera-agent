"""The command reference has to be what the CLI says it is.

Thirty-three of seventy-nine commands appeared in no README and no doc. Writing thirty-three entries
by hand would have fixed that once and gone stale in silence — the command changes, the page does
not, and nothing says so.

So the page is generated from `chimera/_cli_snapshot.json`, which CI already regenerates and diffs
against the real CLI, and this test refuses a page that has drifted from it. That closes the loop: a
command cannot be added without appearing on the page, and an option cannot be renamed without the
page changing with it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gen_command_reference as gen  # noqa: E402


def test_the_page_matches_the_cli() -> None:
    expected = gen.render(json.loads(gen.SNAPSHOT.read_text(encoding="utf-8")))

    assert gen.OUT.read_text(encoding="utf-8") == expected, (
        "docs/commands.md is out of date. Regenerate it:\n"
        "  python scripts/gen_command_reference.py"
    )


def test_every_visible_command_is_on_the_page() -> None:
    """The assertion above compares two strings; this one says what the comparison is FOR.

    A generator that started dropping commands would still match its own output, and the test above
    would stay green while the page emptied.
    """
    snapshot = json.loads(gen.SNAPSHOT.read_text(encoding="utf-8"))
    page = gen.OUT.read_text(encoding="utf-8")

    visible = [c["path"] for c in snapshot["commands"] if not c.get("hidden")]
    assert len(visible) > 50, f"only {len(visible)} commands — is the snapshot stale?"
    missing = [name for name in visible if f"\n## {name}\n" not in page]
    assert not missing, f"commands the CLI has and the page does not: {missing}"


def test_a_hidden_command_stays_off_the_page() -> None:
    """Hidden means hidden. Publishing one would document a thing the CLI declines to advertise."""
    snapshot = json.loads(gen.SNAPSHOT.read_text(encoding="utf-8"))
    fake = {**snapshot, "commands": [{"path": "secret", "name": "secret", "hidden": True}]}

    assert "## secret" not in gen.render(fake)


def test_an_option_appears_with_its_real_flags() -> None:
    """A reference that names an option a user cannot type is worse than no reference."""
    page = gen.OUT.read_text(encoding="utf-8")

    # `--guard` is on `agent` and `solve`, and NOT on `run` — the fact a whole class of wrong
    # instructions turned on.
    agent = page[page.index("\n## agent\n") : page.index("\n## agents\n")]
    assert "`--guard`" in agent
