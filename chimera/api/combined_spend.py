"""What the coding turns running at once have spent together, and the warning when it adds up.

Each turn's meter (:class:`~chimera.orchestration.budget.SpendBudget`) warns when THAT turn has spent
its warning amount. Five conversations running at once could each stay under it and spend five times
it together without a word. So each turn's meter here also reports to one tally, and when two or more
running turns have spent something and their sum crosses a multiple of the warning amount, each of
them says so, once per multiple.

A warning and nothing more: the owner's rule of 2026-09-27 is that a limit where a person is waiting
warns instead of stopping. A ceiling the person typed stays that turn's own, measured on that turn's
spend alone.
"""

from __future__ import annotations

import math
import threading
from typing import Any

from chimera.orchestration.budget import SpendBudget

Notice = tuple[str, str, dict[str, Any]]


class CombinedSpend:
    """The spend of every turn running in this process, by turn id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spent: dict[str, float] = {}

    def budget(self, turn_id: str, *, max_usd: float | None, warn_usd: float | None) -> TurnSpend:
        """The meter for one turn, the same one the agent would build for it, and on the tally."""
        with self._lock:
            self._spent[turn_id] = 0.0
        return TurnSpend(self, turn_id, max_usd=max_usd, warn_usd=warn_usd)

    def report(self, turn_id: str, spent: float) -> tuple[float, int]:
        """Record what one turn has spent so far; return the sum and how many turns have spent."""
        with self._lock:
            if turn_id in self._spent:
                self._spent[turn_id] = spent
            spending = [usd for usd in self._spent.values() if usd > 0]
        return sum(spending), len(spending)

    def close(self, turn_id: str) -> None:
        """A turn that ended leaves the sum."""
        with self._lock:
            self._spent.pop(turn_id, None)


class TurnSpend(SpendBudget):
    """One turn's meter, which also tells the turn what the turns running with it spent."""

    def __init__(self, tally: CombinedSpend, turn_id: str, *, max_usd: float | None, warn_usd: float | None) -> None:
        super().__init__(max_usd or math.inf, warn_usd=warn_usd)
        self._tally = tally
        self._turn_id = turn_id
        self._told_multiple = 0

    def take_notices(self) -> list[Notice]:
        # Read once per step by the agent loop, which is also when this turn's spend is reported.
        out = super().take_notices()
        total, turns = self._tally.report(self._turn_id, self.spent)
        if self.warn_usd is None or turns < 2:
            return out
        multiple = int(total // self.warn_usd)
        if multiple > self._told_multiple:
            self._told_multiple = multiple
            out.append((
                "combined_spend",
                f"the {turns} turns running at once have spent US$ {total:.2f} together",
                {"usd": round(total, 4), "turns": turns},
            ))
        return out
