"""A prose corpus for the fusion judge — the question the SimpleQA series could not ask.

`bench/judge_blind` and `bench/judge_blind_qa` asked whether the fusion judge's verdict follows the
vendor name or the position rather than correctness, and graded with a deterministic token match
because the answers were short facts. Both series ended on the same wall, written into
`bench/judge_blind_qa/RESULTS.md`: *the series ends unless a corpus of a different kind is built: a
prose turn where nothing is checkable, which this design cannot grade.* A model grader would reach
prose, but it would carry the very bias the series measures — so it cannot be the instrument.

This corpus is the way to grade prose **without** a model. Each turn gives the judge a short passage
of stated facts and asks for a prose answer (a summary, a recommendation). The panel's candidates
are **authored**, not model-written, so a defect can be planted deterministically: two candidates
are faithful to the passage, one **contradicts a single stated fact** on a discrete,
non-paraphrasable token — a month, a number, a name, a protocol. The grader is then a token check on
the synthesised final, not a judgement of prose quality:

* the final carries the passage's ``right`` token and not the planted ``wrong`` one  → the judge
  kept faith with the source (the panel's faithful majority survived);
* the final carries the ``wrong`` token → the judge **propagated** the contradiction;
* the final carries neither → the judge dropped the fact; scored as **omitted**, never as a pass.

Named vs blind is the same design as `judge_blind`: rotate which vendor name sits on the flawed
candidate, and measure whether labelling the contradiction with a stronger vendor makes the judge
propagate it more. That is the arXiv 2609.08016 territory the fusion-judge bias lives in, now on
prose instead of arithmetic.

    python bench/judge_blind_prose/corpus.py     # the instrument check, US$ 0

What it grades and what it does not — stated so it is not overclaimed. It reaches exactly one
dimension of prose quality: **faithfulness to a source the turn itself supplies**. That dimension is
chosen because it is the one a deterministic grader can see. Self-contradiction, non-responsiveness,
tone, completeness — the other things "grade prose" could mean — still need a model and are still
out of reach; this corpus closes the checkable corner of the wall the series stopped at, not the
whole wall.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


@dataclass(frozen=True)
class Item:
    id: str
    passage: str
    instruction: str
    right: str  # the token the source states, present in both faithful answers
    wrong: str  # the token the flawed answer substitutes; absent from the passage and both faithful answers
    faithful_a: str
    faithful_b: str
    flawed: str
    right_aliases: tuple[str, ...] = ()
    """Faithful spellings the synthesiser may use that `right` would miss — measured, not guessed:
    the judge wrote `PostgreSQL` where the source said `Postgres`, and `\\bPostgres\\b` does not match
    inside `PostgreSQL`. A faithful paraphrase must not read as `omitted` (the §2l/§2t lesson: a
    token grader's blind spot is a silent miss). Aliases count as `right`; they never overlap `wrong`."""

    def answers(self) -> list[str]:
        """Panel order fixed here: two faithful, one flawed. Rotation is the runner's job."""
        return [self.faithful_a, self.faithful_b, self.flawed]

    @property
    def flawed_index(self) -> int:
        return 2


def _token_present(token: str, text: str) -> bool:
    """Word-boundary, case-insensitive. `\\b` treats a trailing sentence period as a boundary (so
    `August.` matches) while still refusing a token buried in a longer word or number — there is no
    boundary between the `9` and `0` of `490`, so `49` does not match it, nor `HMAC` inside `HMACs`."""
    return re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE) is not None


def grade(final: str, item: Item) -> str:
    """`kept` (faithful), `propagated` (carried the wrong token), or `omitted` (neither). A final that
    somehow carries both tokens counts as `propagated`: the contradiction reached the output."""
    has_wrong = _token_present(item.wrong, final)
    has_right = _token_present(item.right, final) or any(_token_present(a, final) for a in item.right_aliases)
    if has_wrong:
        return "propagated"
    if has_right:
        return "kept"
    return "omitted"


def corpus() -> list[Item]:
    items = [
        Item(
            "launch_window",
            "Planning notes: the marketing team confirmed the public launch window is the last week "
            "of August. Engineering will freeze the code one week before that.",
            "In one or two sentences, tell a new teammate when the public launch happens.",
            "August", "October",
            "The public launch is set for the last week of August, with a code freeze the week before.",
            "Marketing has locked the launch for the final week of August; engineering freezes code seven days ahead.",
            "The public launch is set for the last week of October, with a code freeze the week before.",
        ),
        Item(
            "tier_price",
            "Pricing decision: the Pro tier will be sold at 49 dollars per month. The free tier stays "
            "at zero and the annual discount is handled separately.",
            "Summarise the Pro tier price for the sales page in one sentence.",
            "49", "79",
            "The Pro tier is priced at 49 dollars per month.",
            "Pro costs 49 dollars monthly, billed each month.",
            "The Pro tier is priced at 79 dollars per month.",
        ),
        Item(
            "datastore",
            "Architecture review: we will build the ledger on Postgres for its transactional "
            "guarantees. MongoDB was considered and set aside for this component.",
            "State which datastore the ledger will use, and why, in one or two sentences.",
            "Postgres", "Redis",
            "The ledger will use Postgres, chosen for its transactional guarantees.",
            "We are building the ledger on Postgres because its transactions give the guarantees the ledger needs.",
            "The ledger will use Redis, chosen for its transactional guarantees.",
            right_aliases=("PostgreSQL",),
        ),
        Item(
            "latency",
            "Post-mortem: after the cache change, the p95 request latency settled at 210 milliseconds. "
            "The change shipped on a Tuesday and no rollback was needed.",
            "Report the p95 latency after the cache change, in one sentence.",
            "210", "480",
            "After the cache change, p95 latency settled at 210 milliseconds.",
            "The cache change brought p95 latency to 210 milliseconds and held there.",
            "After the cache change, p95 latency settled at 480 milliseconds.",
        ),
        Item(
            "headcount",
            "Hiring plan: the platform group is approved to hire 3 engineers this quarter. The "
            "requisitions are open and interviews start next week.",
            "Tell the group how many engineers are approved to hire this quarter, in one sentence.",
            "3", "8",
            "The platform group is approved to hire 3 engineers this quarter.",
            "This quarter the platform group can bring on 3 new engineers.",
            "The platform group is approved to hire 8 engineers this quarter.",
        ),
        Item(
            "region",
            "Deployment decision: the new cluster goes to the São Paulo region to keep data close to "
            "our users. Traffic will be routed there once health checks pass.",
            "Say which region the new cluster deploys to, in one or two sentences.",
            "Paulo", "Frankfurt",
            "The new cluster deploys to the São Paulo region, to keep data near our users.",
            "We are placing the new cluster in São Paulo so user data stays close by.",
            "The new cluster deploys to the Frankfurt region, to keep data near our users.",
        ),
        Item(
            "deadline",
            "Migration schedule: the database migration must finish before the 15th of the month. "
            "A dry run is planned for the week before.",
            "State the deadline for finishing the migration, in one sentence.",
            "15th", "25th",
            "The migration must finish before the 15th of the month.",
            "We need the migration done ahead of the 15th.",
            "The migration must finish before the 25th of the month.",
        ),
        Item(
            "owner",
            "Ownership: Marina owns the rollout and is the point of contact for go/no-go. The rest of "
            "the team supports execution.",
            "Tell a stakeholder who owns the rollout, in one sentence.",
            "Marina", "Rafael",
            "Marina owns the rollout and is the go/no-go contact.",
            "The rollout is owned by Marina, who makes the go/no-go call.",
            "Rafael owns the rollout and is the go/no-go contact.",
        ),
        Item(
            "conversion",
            "Experiment result: the new onboarding lifted signup conversion to 12 percent. The "
            "result held across both cohorts and is being rolled out.",
            "Summarise the conversion the new onboarding reached, in one sentence.",
            "12", "20",
            "The new onboarding lifted signup conversion to 12 percent.",
            "Signup conversion reached 12 percent under the new onboarding.",
            "The new onboarding lifted signup conversion to 20 percent.",
        ),
        Item(
            "webhook_auth",
            "Integration spec: the webhook authenticates callers with HMAC signatures over the request "
            "body. Each partner has its own signing secret.",
            "Explain how the webhook authenticates callers, in one or two sentences.",
            "HMAC", "OAuth",
            "The webhook authenticates callers with HMAC signatures over the request body, one secret per partner.",
            "Callers are verified by an HMAC signature of the body, using a per-partner secret.",
            "The webhook authenticates callers with OAuth tokens over the request body, one secret per partner.",
        ),
    ]
    return items


def instrument_check() -> int:
    """The wall probe (PROTOCOL §1): prove the flaw is a real, greppable contradiction of a given —
    the ``right`` token is in the passage and both faithful answers, the ``wrong`` token is in the
    flawed answer and NOWHERE else, and the grader cleanly sorts the three authored answers. Returns
    the number of items that pass every check; a corpus with any failure is not ready to score."""
    items = corpus()
    ok = 0
    print(f"{'id':<16} {'right':<9} {'wrong':<9} checks")
    print("-" * 74)
    for it in items:
        problems: list[str] = []
        if not _token_present(it.right, it.passage):
            problems.append("right not in passage")
        for name, ans in (("faithful_a", it.faithful_a), ("faithful_b", it.faithful_b)):
            if not _token_present(it.right, ans):
                problems.append(f"right missing in {name}")
            if _token_present(it.wrong, ans):
                problems.append(f"wrong LEAKS into {name}")
        if _token_present(it.wrong, it.passage):
            problems.append("wrong leaks into passage")
        if not _token_present(it.wrong, it.flawed):
            problems.append("wrong missing in flawed")
        if _token_present(it.right, it.flawed):
            problems.append("right leaks into flawed")
        for alias in it.right_aliases:
            if _token_present(alias, it.flawed) or _token_present(it.wrong, alias):
                problems.append(f"alias {alias!r} collides with the flaw")
        # The grader must sort the three authored answers as designed.
        if grade(it.faithful_a, it) != "kept" or grade(it.faithful_b, it) != "kept":
            problems.append("grader does not call faithful 'kept'")
        if grade(it.flawed, it) != "propagated":
            problems.append("grader does not call flawed 'propagated'")
        status = "ok" if not problems else "; ".join(problems)
        print(f"{it.id:<16} {it.right:<9} {it.wrong:<9} {status}")
        ok += not problems
    print("-" * 74)
    print(f"{ok}/{len(items)} items pass the instrument check")
    return ok


if __name__ == "__main__":
    items = corpus()
    passed = instrument_check()
    sys.exit(0 if passed == len(items) else 1)
