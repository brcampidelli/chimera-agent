"""Prompt text that promises only what the code does (study 25, `bench/PLAN-study25-system-prompts.md` §4, defect 8).

Two sentences told the model something untrue:
- **The skills block** offered "skills you can use" by name. The agent loop cannot call any of them;
  built-in skills run only inside the evolver and the holdout.
- **The note restored after a compaction** said the conversation "was summarised", even when no
  summariser ran and the span above was a structural note of what had been dropped.
"""

from __future__ import annotations

from chimera.core.context_budget import RunState, compact
from chimera.skills.retrieval import SKILLS_HEADER


def test_the_skills_block_says_it_is_reference_not_tools() -> None:
    assert "not tools you can call" in SKILLS_HEADER
    assert "you can use" not in SKILLS_HEADER


def test_the_restore_note_does_not_claim_a_summary_nobody_wrote() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        *[{"role": "user", "content": f"turn {i}"} for i in range(12)],
    ]
    compacted, changed = compact(messages, keep_recent=4, state=RunState(task="fix the parser"))
    assert changed
    restored = next(m for m in compacted if "context restored after compaction" in str(m.get("content", "")))
    assert "summarised" not in restored["content"]
    assert "compacted" in restored["content"]
