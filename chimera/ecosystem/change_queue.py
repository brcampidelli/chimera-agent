"""Ecosystem governance — govern *change tempo*, not headcount.

When many agents commit into a shared codebase, the risk is integration friction, not
agent count ("Govern the Repository, Not the Agent"). The ChangeQueue regulates the
rate of change with a FIFO merge queue and a batch-size cap, so changes land in
reviewable batches instead of an unbounded stream.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class Change:
    """A proposed change awaiting merge."""

    id: str
    description: str
    author: str = "agent"


class ChangeQueue:
    """A FIFO merge queue with a batch-size cap."""

    def __init__(self, *, batch_size: int = 5) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self.batch_size = batch_size
        self._queue: deque[Change] = deque()
        self.merged: list[Change] = []

    def submit(self, change: Change) -> None:
        self._queue.append(change)

    def pending(self) -> int:
        return len(self._queue)

    def next_batch(self) -> list[Change]:
        """Pop up to ``batch_size`` changes (does not mark them merged)."""
        batch: list[Change] = []
        while self._queue and len(batch) < self.batch_size:
            batch.append(self._queue.popleft())
        return batch

    def merge_batch(self) -> list[Change]:
        """Pop and merge the next batch. Returns the merged changes."""
        batch = self.next_batch()
        self.merged.extend(batch)
        return batch

    def drain(self) -> list[list[Change]]:
        """Merge everything in batch-sized groups. Returns the list of batches."""
        batches: list[list[Change]] = []
        while self.pending():
            batches.append(self.merge_batch())
        return batches
