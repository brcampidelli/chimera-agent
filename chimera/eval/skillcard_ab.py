"""A/B benchmark: reasoning with injected TRS skill cards vs without.

Skill-card injection is only worth enabling if it holds or improves accuracy without
paying too much in tokens. This harness runs the same task suite through the same
backend twice — once with the top-k retrieved cards prepended, once without — and
measures accuracy and token cost for each, plus the accuracy split between tasks that
retrieved a card (a "hit") and those that did not.

Honest expectation: TRS's *token savings* come from shortening long reasoning traces, so
on short-answer suites cards mostly ADD input tokens; the upside there is accuracy, not
cost. The bench surfaces both so the trade-off is explicit before enabling the feature.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chimera.eval.continuous import EvalTask

if TYPE_CHECKING:
    from chimera.eval.paired import PairedResult
from chimera.evolution.card_retrieval import CardIndex, cards_context_block
from chimera.evolution.learned_skill import LearnedSkill
from chimera.providers.gateway import Message, SupportsComplete


@dataclass
class CardABRow:
    task_id: str
    base_ok: bool
    base_tokens: int | None
    card_ok: bool
    card_tokens: int | None
    hit: bool  # a card was retrieved for this task


@dataclass
class CardABReport:
    rows: list[CardABRow] = field(default_factory=list)

    def paired(self) -> PairedResult:
        """The paired (McNemar) comparison of cards vs no-cards over the same tasks.

        This is the honest significance test behind the M19-A1 flip gate: it conditions out the tasks
        where both arms agreed and reports a Wilson CI on the discordant pairs. ``diff_ci`` including 0
        means the accuracy lift is not proven, however large the point estimate.
        """
        from chimera.eval.paired import PairedResult

        a = sum(1 for r in self.rows if r.base_ok and r.card_ok)  # both pass
        b = sum(1 for r in self.rows if r.base_ok and not r.card_ok)  # no-cards won
        c = sum(1 for r in self.rows if not r.base_ok and r.card_ok)  # cards won
        d = sum(1 for r in self.rows if not r.base_ok and not r.card_ok)  # both fail
        return PairedResult("no-cards", "cards", both_pass=a, baseline_only=b, treatment_only=c, both_fail=d)

    def summary(self) -> dict[str, float]:
        n = len(self.rows)
        if not n:
            return {"tasks": 0.0}
        base_acc = sum(r.base_ok for r in self.rows) / n
        card_acc = sum(r.card_ok for r in self.rows) / n
        base_toks = [r.base_tokens for r in self.rows if r.base_tokens is not None]
        card_toks = [r.card_tokens for r in self.rows if r.card_tokens is not None]
        base_avg = sum(base_toks) / len(base_toks) if base_toks else 0.0
        card_avg = sum(card_toks) / len(card_toks) if card_toks else 0.0
        hits = [r for r in self.rows if r.hit]
        misses = [r for r in self.rows if not r.hit]
        return {
            "tasks": float(n),
            "base_accuracy": round(base_acc, 3),
            "card_accuracy": round(card_acc, 3),
            "accuracy_delta_pp": round((card_acc - base_acc) * 100, 1),
            "base_avg_tokens": round(base_avg, 1),
            "card_avg_tokens": round(card_avg, 1),
            "token_delta_pct": round((card_avg / base_avg - 1) * 100, 1) if base_avg else 0.0,
            "pct_hit": round(len(hits) / n * 100, 1),
            "card_acc_on_hit": round(sum(r.card_ok for r in hits) / len(hits), 3) if hits else 0.0,
            "card_acc_on_miss": round(sum(r.card_ok for r in misses) / len(misses), 3)
            if misses
            else 0.0,
        }


def _solve_once(
    backend: SupportsComplete, prompt: str, model: str | None
) -> tuple[str, int | None]:
    result = backend.complete([Message(role="user", content=prompt)], model=model, temperature=0.0)
    tokens: int | None = None
    if result.prompt_tokens is not None or result.completion_tokens is not None:
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
    return result.content, tokens


def run_skillcard_ab(
    backend: SupportsComplete,
    tasks: Iterable[EvalTask],
    cards: list[LearnedSkill],
    *,
    k: int = 3,
    min_overlap: int = 0,
    max_lines: int = 4,
    model: str | None = None,
) -> CardABReport:
    """Run each task with and without injected skill cards against ``backend``.

    ``min_overlap`` (relevance gate) and ``max_lines`` (render budget) mirror the runtime
    ``CardRetriever`` knobs, so the A/B measures the exact injection cost the runtime would pay.
    """
    index = CardIndex(cards)
    report = CardABReport()
    for task in tasks:
        try:
            base_out, base_tok = _solve_once(backend, task.prompt, model)
        except Exception:
            base_out, base_tok = "", None
        retrieved = index.search(task.prompt, k=k, min_overlap=min_overlap)
        block = cards_context_block(retrieved, max_lines_per_card=max_lines)
        card_prompt = f"{block}\n\n{task.prompt}" if block else task.prompt
        try:
            card_out, card_tok = _solve_once(backend, card_prompt, model)
        except Exception:
            card_out, card_tok = "", None
        report.rows.append(
            CardABRow(
                task_id=task.id,
                base_ok=bool(task.check(base_out)),
                base_tokens=base_tok,
                card_ok=bool(task.check(card_out)),
                card_tokens=card_tok,
                hit=bool(retrieved),
            )
        )
    return report


def demo_cards() -> list[LearnedSkill]:
    """A small demo library of reasoning cards relevant to the `hard` suite traps.

    Hand-written to show best-case retrieval relevance; real libraries are distilled
    from the agent's own runs. Use `skillcard-bench --use-store` to bench your own cards.
    """
    return [
        LearnedSkill(
            name="reread_trick_premise",
            description="watch for irrelevant or trick clauses in word problems",
            trigger="a word problem with a surprising or irrelevant action",
            do="identify what is actually asked; use only the relevant quantities",
            avoid="doing arithmetic on numbers that do not affect the answer",
            check="does the answer depend only on the relevant quantities?",
            triggers=["apples", "eat", "buy", "pears", "how many", "left", "sister", "age", "sheep", "die", "alive"],
        ),
        LearnedSkill(
            name="count_letters_exactly",
            description="count letter occurrences character by character",
            trigger="counting how many times a letter appears in a word",
            do="scan character by character and tally each match, then recount",
            avoid="guessing from the word's length or overall shape",
            check="the tally matches on a second pass",
            triggers=["letter", "letters", "appear", "times", "count", "word", "strawberry", "banana", "mississippi"],
        ),
        LearnedSkill(
            name="doubling_one_step_before",
            description="reason about doubling/halving sequences",
            trigger="something doubles each day and is full at day N",
            do="the half-covered day is exactly one step before full (N-1)",
            avoid="dividing N by 2",
            check="full day minus one equals the half day",
            triggers=["double", "doubles", "every day", "half", "cover", "lake", "patch", "lily"],
        ),
        LearnedSkill(
            name="parallel_rate_trap",
            description="rates that do not scale with the count when work is parallel",
            trigger="machines or items produced or drying in parallel",
            do="find one unit's time; parallel units keep that same time",
            avoid="scaling the time by the number of units",
            check="one unit's time equals many units' time when parallel",
            triggers=["machines", "widgets", "towels", "dry", "minutes", "hours", "how long"],
        ),
        LearnedSkill(
            name="count_unchanged_by_action",
            description="a count that an action does not actually change",
            trigger="asking a count after an action that does not change membership",
            do="track whether the entity actually leaves the set",
            avoid="subtracting when nothing left the set",
            check="did anyone or anything actually leave?",
            triggers=["killers", "room", "nobody", "leaves", "how many", "now", "candles", "blow", "blown", "remain"],
        ),
        LearnedSkill(
            name="off_by_one_intervals",
            description="fencepost errors: gaps between events, inclusive ranges",
            trigger="counting time between evenly spaced events, or an inclusive range",
            do="N events have N-1 gaps between them; an inclusive range a..b has b-a+1 items",
            avoid="using the event count as the gap count, or last-first without the +1",
            check="do the gaps (or the inclusive count) reconcile with a tiny worked example?",
            triggers=["strike", "strikes", "clock", "pill", "pills", "every", "minutes", "seconds", "pages", "page", "inclusive", "read"],
        ),
        LearnedSkill(
            name="overtaking_keeps_place",
            description="overtaking takes the passed runner's rank, not first place",
            trigger="a race where you overtake the runner in Nth place",
            do="passing the runner in Nth place puts you in Nth place — you take their spot",
            avoid="answering first, or shifting by the wrong number of places",
            check="you cannot be ahead of runners you have not yet passed",
            triggers=["overtake", "overtook", "race", "place", "runner", "pass", "passed"],
        ),
        LearnedSkill(
            name="gamblers_fallacy_independent",
            description="independent trials have no memory of past outcomes",
            trigger="a probability question after a streak of independent outcomes",
            do="a fair coin is 50% on the next flip regardless of the prior streak",
            avoid="letting the run of heads or tails change the stated probability",
            check="are the trials actually independent? then the streak is irrelevant",
            triggers=["coin", "heads", "tails", "row", "probability", "percent", "chance", "flip"],
        ),
        LearnedSkill(
            name="rate_scales_with_both",
            description="output scales with workers AND time when both change",
            trigger="a rate problem where both the worker count and the duration change",
            do="reduce to one worker's rate per unit time, then scale by workers times periods",
            avoid="assuming the answer equals the original count when both inputs scaled",
            check="does one unit's rate, scaled by both factors, reproduce the totals?",
            triggers=["hens", "eggs", "lay", "days", "produce"],
        ),
        LearnedSkill(
            name="trick_calendar_fact",
            description="watch for trick general-knowledge premises",
            trigger="a general-knowledge question with a hidden trivial answer",
            do="every month has at least 28 days, so all 12 do",
            avoid="pattern-matching to the 'special' item (February) instead of reading literally",
            check="does the literal reading give a simpler, larger answer?",
            triggers=["months", "month", "28", "days", "year"],
        ),
    ]
