"""Measured promote/demote decisions for the skill lifecycle loop (M18-4).

arXiv 2607.07052 (triple-corroborated) closes an economic loop production agents leave open: promote
a behaviour to a cheap standing path only after **N verified validations**, and **auto-demote it on
measured regression**. Chimera already had the demote *signal* (:meth:`SkillStore.retirement_candidates`)
but applied it manually, and had no promotion tier at all. This policy computes both transitions from
the store's measured usage stats — never a model's self-report — so the loop can run automatically:

- **promote**: a ``provisional`` skill (on probation) that has enough uses and a high win rate → ``active``.
- **demote**: a ``provisional`` that failed probation OR an ``active`` skill whose win rate regressed,
  each with enough uses → ``retired`` (proposed-with-review is preserved; ``--apply`` closes the loop).

A skill in between (enough uses, middling rate) stays provisional — keep observing, don't decide early.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LifecycleDecisions:
    """Names to move, by transition (empty lists when nothing crosses a threshold)."""

    promote: list[str] = field(default_factory=list)  # provisional -> active (proven)
    demote: list[str] = field(default_factory=list)   # provisional/active -> retired (failed/regressed)


class SkillLifecyclePolicy:
    """Turns measured per-skill stats into promote/demote decisions (pure, no I/O)."""

    def __init__(
        self,
        *,
        promote_min_uses: int = 5,
        promote_min_rate: float = 0.7,
        demote_min_uses: int = 5,
        demote_max_rate: float = 1 / 3,
    ) -> None:
        self.promote_min_uses = promote_min_uses
        self.promote_min_rate = promote_min_rate
        self.demote_min_uses = demote_min_uses
        self.demote_max_rate = demote_max_rate

    def decide(self, stats: list[dict[str, object]]) -> LifecycleDecisions:
        """Compute transitions from ONE model+toolset slice of :meth:`SkillStore.stats` rows."""
        out = LifecycleDecisions()
        for row in stats:
            status = str(row.get("status", "active"))
            uses = row.get("uses")
            rate = row.get("rate")
            if not isinstance(uses, int) or not isinstance(rate, float):
                continue  # never used enough to have a measured rate — leave it alone
            name = str(row.get("name", ""))
            if status == "provisional" and uses >= self.promote_min_uses and rate >= self.promote_min_rate:
                out.promote.append(name)
            elif status in ("provisional", "active") and uses >= self.demote_min_uses and rate <= self.demote_max_rate:
                out.demote.append(name)
        return out

    def decide_slices(self, slices: list[list[dict[str, object]]]) -> LifecycleDecisions:
        """Decide each context on its own evidence, then combine with refusals winning.

        A skill demoted in any context is demoted; one promoted in some context is promoted only if
        no context demotes it. Evidence is never summed across contexts.
        """
        promote: list[str] = []
        demote: list[str] = []
        for stats in slices:
            decided = self.decide(stats)
            demote.extend(n for n in decided.demote if n not in demote)
            promote.extend(n for n in decided.promote if n not in promote)
        return LifecycleDecisions(promote=[n for n in promote if n not in demote], demote=demote)
