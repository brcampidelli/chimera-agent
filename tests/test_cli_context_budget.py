"""We documented a `--context-budget` flag that did not exist.

`apps/desktop/src/components/code/Conversation.tsx` says, in as many words, that the app and
`chimera solve --context-budget` behave the same at the same number. The flag was never there:
`grep -rn "context.budget" chimera/cli/` came back empty, and of the sixteen `AgentConfig(`
constructions in `chimera/cli/main.py`, zero passed one.

So every terminal surface — solve, cron, CI, a systemd unit — ran with the message list only ever
growing, and had no way to ask otherwise. Overflow is TERMINAL: `chimera/providers/failover.py`
maps `CONTEXT_OVERFLOW` to `ABORT`, so a long run died on the provider's error rather than
shrinking its prompt.

The library default does NOT change. `AgentConfig.context_budget` stays `None` — "off by default
because compaction discards messages, and a caller that has not asked for that should not get it".
What changes is that a CLI caller can ask at all.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "chimera" / "cli" / "main.py"
CONVERSATION = ROOT / "apps" / "desktop" / "src" / "components" / "code" / "Conversation.tsx"


def test_the_flag_the_desktop_promises_exists() -> None:
    assert "--context-budget" in CLI.read_text(encoding="utf-8")


def test_every_worker_solve_builds_can_receive_it() -> None:
    """Not "one of them". The escalated worker runs every attempt after the first, so a budget that
    reaches only the cheap worker compacts on attempt 1 and stops compacting for the rest of the run
    — which is the half of a run most likely to have grown a long prompt."""
    assert CLI.read_text(encoding="utf-8").count("context_budget=context_budget") == 2


def test_the_default_is_still_off() -> None:
    """The decision written in `AgentConfig` is not being overturned by a CLI flag.

    Compaction discards messages. Someone who upgrades and runs the same command must get the same
    behaviour they got before — the flag is a way to ask, not a new default.
    """
    from chimera.core.agent import AgentConfig

    assert AgentConfig().context_budget is None


def test_the_desktop_comment_names_a_flag_that_exists() -> None:
    """The ratchet on the claim itself.

    This test is the reason the fix is a flag rather than a comment edit: whichever way that
    argument had gone, the state where the sentence and the CLI disagree must not be reachable
    again. If the flag is ever removed, this fails here rather than being discovered by a user
    typing what our own UI told them to type.
    """
    text = CONVERSATION.read_text(encoding="utf-8")
    for flag in re.findall(r"chimera solve (--[a-z-]+)", text):
        assert flag in CLI.read_text(encoding="utf-8"), f"the app promises {flag} and the CLI has no such option"
