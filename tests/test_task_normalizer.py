"""Tests for the bug-report task normalizer (arXiv 2607.07593)."""

from __future__ import annotations

from chimera.core.task_normalizer import looks_like_bug_report, normalize_task

_LONG_BUG = (
    "So I was working on the project late last night and I noticed something really weird "
    "happening when I tried to run the pipeline, it took me a while to even figure out what "
    "was going on because there was so much output scrolling past on my terminal and honestly "
    "I almost gave up. Anyway, after a lot of digging around I found that in chimera/core/agent.py:88 "
    "there is a KeyError: 'result' being raised. Expected the loop to return a dict but got None. "
    "To reproduce: run pytest tests/test_agent.py and watch it fail. I think the fix is that the "
    "return should be the parsed payload instead of the raw response. It's been driving me crazy."
)


def test_detects_bug_report() -> None:
    assert looks_like_bug_report("This throws a KeyError when I run it")
    assert looks_like_bug_report("expected 200 but got 500")
    assert not looks_like_bug_report("Write a haiku about the ocean.")


def test_short_task_is_unchanged() -> None:
    task = "Fix the bug in agent.py"  # bug-ish but short — nothing to trim
    assert normalize_task(task) == task


def test_non_bug_long_task_is_unchanged() -> None:
    task = "Write a detailed essay about the history of Rome. " * 20
    assert normalize_task(task) == task


def test_long_bug_report_is_normalized() -> None:
    out = normalize_task(_LONG_BUG)
    assert out != _LONG_BUG
    assert out.startswith("Normalized bug report")
    # Salient facts are surfaced up front.
    assert "chimera/core/agent.py:88" in out
    assert "KeyError" in out
    assert "Expected" in out or "expected" in out
    assert "reproduce" in out.lower()
    assert "fix" in out.lower()
    # The original narrative is kept but trimmed.
    assert "Original report:" in out
    assert "trimmed" in out


def test_long_narrative_is_capped() -> None:
    # For a genuinely long ramble the retained narrative is bounded, so the normalized form is smaller
    # than the raw report (the paper's "cut long narrative" lever). A moderate report just gains a
    # structure-first header (additive) — the win there is ordering, not raw length.
    huge = _LONG_BUG + (" and then even more rambling backstory that adds nothing useful. " * 100)
    out = normalize_task(huge)
    assert "[original report trimmed" in out
    assert len(out) < len(huge)


def test_bugword_but_no_salient_fields_is_unchanged() -> None:
    # Trips the trigger but has no file/error/expected/repro/fix line to extract → left alone.
    task = "This is broken and it fails and it does not work at all and I am frustrated. " * 8
    assert normalize_task(task) == task
