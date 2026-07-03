"""Tests for the skill-card A/B bench (no network)."""

from __future__ import annotations

from typing import Any

from chimera.eval.continuous import EvalTask
from chimera.eval.skillcard_ab import CardABReport, demo_cards, run_skillcard_ab
from chimera.providers import CompletionResult


class _CardSensingBackend:
    """Answers 'RIGHT' only when the prompt carries an injected card block."""

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        last = messages[-1]
        prompt = last.content if hasattr(last, "content") else str(last.get("content", ""))
        has_cards = "Retrieved reasoning skills" in prompt
        return CompletionResult(
            content="RIGHT" if has_cards else "WRONG",
            model="fake",
            prompt_tokens=20 if has_cards else 10,
            completion_tokens=2,
        )


def test_skillcard_ab_improves_with_a_relevant_card() -> None:
    task = EvalTask(
        "letters", "How many times does the letter r appear in strawberry?", lambda o: o == "RIGHT"
    )
    report = run_skillcard_ab(_CardSensingBackend(), [task], demo_cards(), k=2)
    row = report.rows[0]
    assert row.hit is True  # a relevant card was retrieved
    assert row.base_ok is False and row.card_ok is True
    summary = report.summary()
    assert summary["accuracy_delta_pp"] == 100.0
    assert summary["card_acc_on_hit"] == 1.0
    assert summary["token_delta_pct"] > 0  # injecting cards added input tokens


def test_skillcard_ab_no_hit_when_nothing_matches() -> None:
    # All-nonsense tokens: none appear in any demo card's name/description/triggers.
    task = EvalTask("unrelated", "Xyzzy plugh frobnicate quux.", lambda o: True)
    report = run_skillcard_ab(_CardSensingBackend(), [task], demo_cards(), k=2)
    assert report.rows[0].hit is False


def test_demo_cards_all_carry_content() -> None:
    cards = demo_cards()
    assert cards and all(c.has_card() for c in cards)


def test_empty_report_summary() -> None:
    assert CardABReport().summary() == {"tasks": 0.0}
