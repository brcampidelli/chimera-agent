"""How independent are our fusion panel's votes? Measured, on runs we already paid for. US$0.

Three benches left per-member answers on disk. Two of them can answer this and one cannot, which
is itself the instrument check:

* `bench/fusion_aggregate/results/panel.jsonl` — 3 frontier models, 40 arithmetic items, accuracies
  **0.95 / 0.975 / 1.00**. Three errors in 120 cells. At ceiling, so there is nothing to correlate;
  reported and then set aside (§2q).
* `bench/judge_blind_hard/results/collect-all.jsonl` — 3 cheaper models, **50 AIME items**,
  accuracies 0.60 / 0.70 / 0.70, already scored per writer. This is the corpus.

**Why the question matters.** arXiv 2609.10969 (2,880 scenarios) measured cross-model voting over
**shared** evidence approving 62.9% of unsafe proposals against 22.9% with independent sources — a
40.9 pp source effect against an 11.3 pp model effect. Our panel varies the model and shares the
evidence by construction, and `chimera/evolution/auto_evolve.py` reads "3 of 3 transfer models
passed" as three confirmations. Whether it is three is an empirical question nobody had asked.

Uses the shipped `chimera.eval.replicated.icc1` and `design_effect` for the same reason
`bench/design_effect` did: a number that has to be comparable to an existing one must come out of
the existing code.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from chimera.eval.replicated import design_effect, icc1

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

HARD = REPO / "bench/judge_blind_hard/results/collect-all.jsonl"
EASY = REPO / "bench/fusion_aggregate/results/panel.jsonl"
_ANSWER = re.compile(r"ANSWER:\s*([\-0-9][0-9,.]*)", re.I)


@dataclass(frozen=True)
class Panel:
    """One corpus: who answered, and a member × item grid of correct/incorrect."""

    name: str
    members: list[str]
    grid: list[list[bool]]
    """``grid[member][item]``."""

    @property
    def accuracies(self) -> list[float]:
        return [sum(row) / len(row) for row in self.grid]

    @property
    def by_item(self) -> list[list[bool]]:
        """Transposed: ``[item][member]`` — the shape `icc1` wants, items as the clusters."""
        return [list(column) for column in zip(*self.grid, strict=True)]


def _norm(text: str) -> str:
    clean = text.replace(",", "").strip()
    return clean.rstrip("0").rstrip(".") if "." in clean else clean


def load_hard() -> Panel:
    rows = [json.loads(line) for line in HARD.read_text(encoding="utf-8").splitlines() if line.strip()]
    cells: dict[str, dict[str, bool]] = defaultdict(dict)
    for row in rows:
        for writer, correct in zip(row["writers"], row["correct"], strict=True):
            cells[writer][row["item_id"]] = bool(correct)
    members = sorted(cells)
    ids = [r["item_id"] for r in rows]
    return Panel("judge_blind_hard (AIME, 3 cheap models)", members,
                 [[cells[m][i] for i in ids] for m in members])


def load_easy() -> Panel | None:
    """The ceiling corpus, when it is on this machine.

    `bench/fusion_aggregate/results/` is gitignored, so this half is reproducible only where the run
    happened. What it showed is recorded in RESULTS.md rather than depending on a file the repo does
    not carry; the corpus that decides anything (`judge_blind_hard`) IS tracked.
    """
    if not EASY.is_file():
        return None
    rows = [json.loads(line) for line in EASY.read_text(encoding="utf-8").splitlines() if line.strip()]
    cells: dict[str, dict[str, bool]] = defaultdict(dict)
    for row in rows:
        for answer in row["answers"]:
            found = _ANSWER.search(answer.get("content") or "")
            cells[answer["model"]][row["id"]] = bool(
                found and _norm(found.group(1)) == _norm(row["gold"])
            )
    members = sorted(cells)
    ids = [r["id"] for r in rows]
    return Panel("fusion_aggregate (arithmetic, 3 frontier models)", members,
                 [[cells[m][i] for i in ids] for m in members])


def expected_distribution(accuracies: list[float], items: int) -> dict[int, float]:
    """How many items would have k members correct, if the members were INDEPENDENT.

    The exact Poisson-binomial, not an approximation — with three members it is one convolution.
    This is the null the observed distribution is read against: agreement beyond it is the panel
    sharing something, which for a panel that varies model and shares the question is the question.
    """
    distribution = {0: 1.0}
    for p in accuracies:
        nxt: dict[int, float] = defaultdict(float)
        for k, weight in distribution.items():
            nxt[k + 1] += weight * p
            nxt[k] += weight * (1.0 - p)
        distribution = dict(nxt)
    return {k: v * items for k, v in sorted(distribution.items())}


def effective_votes(panel: Panel) -> tuple[float | None, float | None]:
    """(ICC over items, effective independent votes) for a panel of this size."""
    icc, _why = icc1(panel.by_item)
    if icc is None:
        return None, None
    deff = design_effect(icc, len(panel.members))
    assert deff is not None
    return icc, len(panel.members) / deff


def report(panel: Panel) -> dict:
    items = len(panel.grid[0])
    observed = Counter(sum(1 for m in column if m) for column in panel.by_item)
    expected = expected_distribution(panel.accuracies, items)
    icc, votes = effective_votes(panel)

    print(f"\n=== {panel.name} — {len(panel.members)} members, {items} items ===")
    for member, accuracy in zip(panel.members, panel.accuracies, strict=True):
        print(f"   {member:46s} {accuracy:.3f}")

    print(f"\n   {'members correct':>16} {'observed':>9} {'if independent':>15}")
    for k in range(len(panel.members) + 1):
        print(f"   {k:>16} {observed.get(k, 0):>9} {expected.get(k, 0.0):>15.1f}")

    unanimous = observed.get(0, 0) + observed.get(len(panel.members), 0)
    unanimous_expected = expected.get(0, 0.0) + expected.get(len(panel.members), 0.0)
    print(f"\n   unanimous (all right or all wrong): {unanimous} observed,"
          f" {unanimous_expected:.1f} if independent")
    if icc is None:
        print("   ICC: not computable — no variance to partition")
    else:
        assert votes is not None
        print(f"   ICC(1) over items {icc:+.3f}  ->  {len(panel.members)} members carry "
              f"{votes:.2f} independent votes")
    return {
        "corpus": panel.name,
        "members": panel.members,
        "accuracies": [round(a, 4) for a in panel.accuracies],
        "items": items,
        "observed_k_correct": dict(sorted(observed.items())),
        "expected_if_independent": {k: round(v, 2) for k, v in expected.items()},
        "icc": None if icc is None else round(icc, 4),
        "effective_votes": None if votes is None else round(votes, 3),
    }


def main() -> None:
    out = []
    easy = load_easy()
    if easy is None:
        print("fusion_aggregate/results is gitignored and absent here — see RESULTS.md §1")
    else:
        out.append(report(easy))
    out.append(report(load_hard()))
    (HERE / "results.json").write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print("\nwrote results.json")


if __name__ == "__main__":
    main()
