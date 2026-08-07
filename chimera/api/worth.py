"""Was it worth it? — what each profile actually cost, and what it actually got.

Three numbers per configuration, over the runs that really happened in this workspace: did it pass,
what did it cost, how many attempts did it take. Nothing modelled, nothing predicted.

The value of this view is that no vendor can produce it. A benchmark published by whoever built the
agent is always suspect — the tasks are theirs, the tuning is theirs, and the reader has no way to
check. A benchmark that ran on **your** repo, against **your** verify command, is a different kind
of claim. That is the only durable advantage here, and it survives exactly as long as the numbers
stay honest, so the refusals below are the feature.

**It refuses to add up costs it does not fully know.** One attempt with an unpriced model makes the
whole run's cost ``None``, and one such run makes the group's total ``None`` — reported alongside
how many runs *were* priced, so a reader can see the coverage rather than a plausible-looking sum.
A partial sum is not conservative; it is confidently wrong in one direction, and the direction is
"whichever configuration used a free tier looks cheaper than it was".

**It refuses to declare a winner.** No ranking, no "best profile", no highlight. These are
observational groups from whatever the user happened to run: different tasks, different days,
different difficulty, no randomisation. The comparison that can support a verdict is the paired A/B
in ``bench/role_routing``, which is pre-registered and unrun. Sorting by pass rate here would look
like the same thing and would be a completely different, much weaker claim wearing its clothes.

**It refuses to attribute old runs to a profile.** Receipts written before the field existed carry
``profile = null`` and stay in their own group. Folding them into the default would put fabricated
evidence into the one view whose entire job is to say whether a configuration earned its cost.

**It reports n, always, and says when n is too small to read.** Three runs is an anecdote. The view
says so rather than letting a 100% pass rate over two runs look like a finding.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.api.runs import RunReceipt

#: Below this many runs a group is reported as "too few to read". Not a significance threshold —
#: there is no test here to be significant. It is the point below which a pass rate is a story about
#: which tasks happened to get run, and saying so is cheaper than being asked later.
READABLE_N = 10


class ProfileWorth(BaseModel):
    """One configuration's record, over the runs that actually happened here."""

    profile: str | None
    """The configuration label, or ``null`` for runs that named none (including every run written
    before the field existed)."""

    profile_source: str = "user"
    """Who chose it: ``user`` or ``system``. Its own group, never folded into the same label — the
    screen stopped asking, so most new runs carry a default nobody picked, and counting those beside
    deliberate choices would quietly answer "was this configuration worth it?" with runs that were
    never a configuration decision at all."""

    runs: int
    passed: int
    passed_by_verifier: int
    """Of ``passed``, how many were judged by something EXECUTABLE.

    Without this column the table was silently merging two different claims. A run whose winning
    attempt carries ``evidence == "verifier"`` was approved by a command's exit code. Every other
    passing run was approved by a Manager LLM reading the answer text — it never sees the diff, the
    transcript, or a file (``chimera/core/autonomous.py:684-690``), and the only thing separating it
    from "the model wrote a convincing paragraph" is that some file changed.

    Both are true uses of the word "passed", and the run really did pass. But a view whose entire job
    is to say whether a configuration earned its cost cannot count them as the same evidence — that
    is the thing this module's own docstring refuses to do with profiles, applied to verdicts."""

    reverted: int
    """Runs where at least one attempt was made and undone — work that was done and thrown away."""
    unproductive: int
    """Runs whose successful attempt changed no file. Counted separately because a pass that edited
    nothing is the empty-patch failure this project measured and fixed; folding it into `passed`
    would let a configuration look good at the exact thing it is bad at."""

    attempts_total: int
    """Summed attempts. A mean is not published: with n in single digits it invites a comparison of
    two numbers that differ by less than one run."""

    usd_total: float | None
    """Cost across the group, or ``null`` when any run's cost was unknown."""
    usd_known_runs: int
    """How many runs had a fully-known cost. The denominator for reading ``usd_total`` — without it
    a ``null`` is indistinguishable from "no data" and a number is indistinguishable from "all of
    it"."""

    @property
    def readable(self) -> bool:
        return self.runs >= READABLE_N


class WorthReport(BaseModel):
    """Every configuration seen in the run log, in a fixed order, with the caveat attached."""

    profiles: list[ProfileWorth]
    total_runs: int
    readable_n: int = READABLE_N
    #: True when at least one group has enough runs to be worth reading. The UI uses this to decide
    #: whether to show the numbers as a record or as "not enough yet" — never to hide them.
    any_readable: bool = False


def summarize_worth(receipts: list[RunReceipt]) -> WorthReport:
    """Group finished runs by profile and count what happened. No inference, no ranking."""
    # Keyed on WHO CHOSE the profile as well as which one. A run somebody picked "max" for and a run
    # that got "balanced" because the screen stopped asking are not the same evidence, and merging
    # them would be permanent: this module already refuses to back-attribute old receipts for the
    # same reason. The label is the UI's problem; the separation is this function's.
    groups: dict[tuple[str | None, str], list[RunReceipt]] = defaultdict(list)
    for receipt in receipts:
        # A paused run has not reached a verdict — counting it as a failure would blame a
        # configuration for a decision the human has not made yet.
        if getattr(receipt, "paused", False):
            continue
        groups[(receipt.profile or None, getattr(receipt, "profile_source", "user"))].append(receipt)

    out: list[ProfileWorth] = []
    for (profile, profile_source), runs in groups.items():
        priced = [r for r in runs if r.usd is not None]
        out.append(
            ProfileWorth(
                profile=profile,
                profile_source=profile_source,
                runs=len(runs),
                passed=sum(1 for r in runs if r.success),
                # The WINNING attempt's evidence, not any attempt's: a run that failed twice with a
                # verifier and then passed on a manager-only retry was not verified.
                passed_by_verifier=sum(
                    1
                    for r in runs
                    if r.success
                    and any(a.success and a.evidence == "verifier" for a in r.attempts)
                ),
                reverted=sum(1 for r in runs if any(a.reverted for a in r.attempts)),
                # Only successes: an unproductive FAILURE is just a failure, and counting it here
                # would double-punish the same run in two columns.
                unproductive=sum(
                    1
                    for r in runs
                    if r.success and any(a.success and a.diff_productive is False for a in r.attempts)
                ),
                attempts_total=sum(len(r.attempts) for r in runs),
                usd_total=(
                    round(sum(r.usd or 0.0 for r in priced), 6) if len(priced) == len(runs) and runs
                    else None
                ),
                usd_known_runs=len(priced),
            )
        )

    # Sorted by NAME, never by outcome. Ordering these by pass rate would read as a ranking, and a
    # ranking is a claim these observational groups cannot support — different tasks, different
    # days, no randomisation. The pre-registered paired A/B is what can support one.
    out.sort(key=lambda p: (p.profile is None, p.profile or ""))
    return WorthReport(
        profiles=out,
        total_runs=sum(p.runs for p in out),
        any_readable=any(p.readable for p in out),
    )
