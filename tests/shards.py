"""Split the suite into disjoint shards by test file, for a CI runner that can no longer hold it whole.

The Windows job ran every test on one runner and outgrew its limit. On #903 (2026-10-15) pytest
finished in 44:35 — 11,816 passed, nothing failed — and the job was cancelled at 45:00 anyway; its
green runs on `main` the day before took 34 to 41 minutes. A timeout that fires with no failure reads
as red, costs a re-run of the whole job and, because that job waits for `quality`, holds every merge.
Raising the limit again only moves the edge: the suite grows with every PR.

**By file, never by test.** Module- and class-scoped fixtures, and the order tests inside a module
run in, stay exactly what they are on a developer's machine. A shard is a set of whole files.

**Balanced by collected test count**, largest file first onto the lightest shard. Count is a proxy
for time, not a measurement — a file of ten slow subprocess tests weighs less here than it costs —
but it needs no timing data checked in and no new dependency, and every shard can compute it alone.

**Deterministic.** Each shard computes the same assignment from the same collection, so together the
shards run every file exactly once. That property is what makes a split safe, and it is the one
`test_the_ci_shards_cover_every_test_file_once.py` pins — including against the workflow listing
three shards while telling each one it is "of 2".
"""

from __future__ import annotations

from collections.abc import Mapping

#: The variable a CI job sets to ``"<index>/<total>"`` (1-based). Unset or empty runs everything.
ENV = "CI_TEST_SHARD"


def parse(spec: str) -> tuple[int, int]:
    """``"2/3"`` → ``(2, 3)``. Anything else is a ValueError, never "run everything".

    A malformed value silently running the whole suite would hide a broken matrix behind green
    runs that each took the full 45 minutes; silently running nothing would be worse.
    """
    index_text, sep, total_text = spec.strip().partition("/")
    if not sep or not index_text.strip().isdigit() or not total_text.strip().isdigit():
        raise ValueError(f"{ENV} must look like '2/3', got {spec!r}")
    index, total = int(index_text), int(total_text)
    if total < 1 or not 1 <= index <= total:
        raise ValueError(f"{ENV}={spec!r}: the index must be between 1 and the total")
    return index, total


def assign(counts: Mapping[str, int], total: int) -> dict[str, int]:
    """Each file's shard (1-based), from how many tests each file holds.

    Sorted by (-count, path) before placing so the answer does not depend on collection order or on
    the mapping's insertion order. Ties between equally loaded shards go to the lowest index.
    """
    if total < 1:
        raise ValueError("total must be at least 1")
    loads = [0] * total
    owner: dict[str, int] = {}
    for path, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        lightest = min(range(total), key=lambda shard: (loads[shard], shard))
        loads[lightest] += count
        owner[path] = lightest + 1
    return owner
