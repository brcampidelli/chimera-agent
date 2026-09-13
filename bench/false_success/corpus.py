"""The corpus: one Harness-Bench solve = one claim text + one label written by a different process.

The label never sees the text. `answer` is what the agent said it did; `outcome_score` is what the
benchmark's oracle measured. That is the whole point — no annotation to do, no judge to trust.

Nothing here is redistributable: the Harness-Bench task material has no licence file, so this reads
the local run (`~/hb-homes`, `~/harness-bench`) and the repo keeps only counts. See PREREGISTRATION §10.

    python corpus.py            # the instrument check of PREREGISTRATION §2 and §3
"""

from __future__ import annotations

import difflib
import glob
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

PASS_THRESHOLD = 0.8
"""An oracle score at or above this is a pass. Same threshold `read_results.py` uses for #453."""

_HID = re.compile(r"^(?P<task>.*)-(?P<hid>arm-\d{3}-r\d)$")


@dataclass(frozen=True)
class Solve:
    """One run of one task under one arm."""

    task: str
    hid: str
    claim: str
    """The agent's final completion claim, in its own words. Truncated at 2,000 chars by the writer."""
    self_report: bool
    """The agent's own `success` boolean — the gate Chimera ships today."""
    oracle: float
    """The benchmark's independent grade. Never sees `claim`."""
    rounds: int
    attempts: int
    reverted: bool
    stagnant: bool
    delivered_matches_verified: bool | None
    """TRI-STATE, and coercing it to bool is a real trap — it cost this bench a wrong finding.

    `chimera/core/autonomous.py:_delivered_matches_verified` compares the delivered tree's digest
    against the winning attempt's `verified_fingerprint`: True (byte-identical), False (the verdict
    is about a tree that no longer exists) or **None when there is no winning attempt to compare
    against**. `bool(None)` is False, which silently turns "we could not look" into "we looked and
    it did not match" — and makes the field look like a copy of `success`, which it is not.
    """

    @property
    def passed(self) -> bool:
        return self.oracle >= PASS_THRESHOLD

    @property
    def false_success(self) -> bool:
        return self.self_report and not self.passed


def _oracle_of(bench_home: Path, hid: str, task: str) -> float | None:
    hits = glob.glob(str(bench_home / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    if not hits:
        return None
    with open(hits[0], encoding="utf-8") as handle:
        payload = json.load(handle)
    value = (payload.get("oracle_result") or {}).get("outcome_score")
    return float(value) if isinstance(value, (int, float)) else None


def load(
    homes: Path | None = None, bench_home: Path | None = None
) -> tuple[list[Solve], dict[str, int]]:
    """Read every solve directory. Returns (solves, why-some-were-dropped)."""
    homes = homes or Path(os.path.expanduser("~/hb-homes"))
    bench_home = bench_home or Path(os.path.expanduser("~/harness-bench"))

    solves: list[Solve] = []
    dropped: Counter[str] = Counter()
    for directory in sorted(glob.glob(str(homes / "*"))):
        match = _HID.match(os.path.basename(directory))
        if not match:
            dropped["not a factorial arm"] += 1
            continue
        task, hid = match.group("task"), match.group("hid")
        receipt = Path(directory) / "runs.jsonl"
        if not receipt.is_file():
            dropped["no receipt"] += 1
            continue
        lines = [
            json.loads(raw)
            for raw in receipt.read_text(encoding="utf-8").splitlines()
            if raw.strip()
        ]
        if not lines:
            dropped["empty receipt"] += 1
            continue
        oracle = _oracle_of(bench_home, hid, task)
        if oracle is None:
            dropped["no oracle score"] += 1
            continue
        last = lines[-1]
        tries = last.get("attempts") or []
        solves.append(
            Solve(
                task=task,
                hid=hid,
                claim=last.get("answer") or "",
                self_report=bool(last.get("success")),
                oracle=oracle,
                rounds=len(lines),
                attempts=len(tries),
                reverted=any(bool(a.get("reverted")) for a in tries),
                stagnant=bool(last.get("stagnant")),
                delivered_matches_verified=last.get("delivered_matches_verified"),
            )
        )
    return solves, dict(dropped)


def askable_tasks(solves: list[Solve]) -> list[str]:
    """Tasks where the within-task question exists at all: both classes present.

    A constant-label task contributes nothing — nothing about a claim could move a label that never
    varies. Excluding them is not cherry-picking; including them would be averaging in zeros.
    """
    by_task: dict[str, list[Solve]] = defaultdict(list)
    for solve in solves:
        by_task[solve.task].append(solve)
    return sorted(
        task
        for task, group in by_task.items()
        if any(s.passed for s in group) and any(not s.passed for s in group)
    )


def stats(solves: list[Solve], dropped: dict[str, int]) -> dict:
    """Counts only — no text. This is the artifact the repo is allowed to carry."""
    by_task: dict[str, list[Solve]] = defaultdict(list)
    for solve in solves:
        by_task[solve.task].append(solve)
    askable = askable_tasks(solves)
    lengths = [len(s.claim) for s in solves]
    pairs = sum(
        sum(1 for s in by_task[t] if s.passed) * sum(1 for s in by_task[t] if not s.passed)
        for t in askable
    )
    return {
        "solves_usable": len(solves),
        "dropped": dropped,
        "joint_self_report_x_oracle": {
            "claimed_success_oracle_pass": sum(1 for s in solves if s.self_report and s.passed),
            "claimed_success_oracle_fail": sum(1 for s in solves if s.false_success),
            "claimed_failure_oracle_pass": sum(
                1 for s in solves if not s.self_report and s.passed
            ),
            "claimed_failure_oracle_fail": sum(
                1 for s in solves if not s.self_report and not s.passed
            ),
        },
        "claim_chars": {
            "empty": sum(1 for n in lengths if n == 0),
            "min": min(lengths),
            "median": statistics.median(lengths),
            "max": max(lengths),
            "at_truncation_cap": sum(1 for n in lengths if n >= 2000),
        },
        "exact_duplicate_claims": len(solves) - len({s.claim for s in solves}),
        "delivered_matches_verified": {
            "true": sum(1 for s in solves if s.delivered_matches_verified is True),
            "false": sum(1 for s in solves if s.delivered_matches_verified is False),
            "none_not_checkable": sum(1 for s in solves if s.delivered_matches_verified is None),
        },
        "tasks_total": len(by_task),
        "tasks_askable": len(askable),
        "items_in_askable_tasks": sum(len(by_task[t]) for t in askable),
        "discordant_pairs": pairs,
        "per_task_pass_rate": {
            t: round(sum(1 for s in g if s.passed) / len(g), 3) for t, g in sorted(by_task.items())
        },
    }


def instrument_check() -> None:
    solves, dropped = load()
    report = stats(solves, dropped)
    joint = report["joint_self_report_x_oracle"]
    claimed = joint["claimed_success_oracle_pass"] + joint["claimed_success_oracle_fail"]
    failures = joint["claimed_success_oracle_fail"] + joint["claimed_failure_oracle_fail"]

    print(f"usable solves: {report['solves_usable']}   dropped: {dropped}")
    print("\n-- the phenomenon is present (PREREGISTRATION §2) --")
    print(f"  claimed success, oracle agrees  : {joint['claimed_success_oracle_pass']}")
    print(f"  claimed success, oracle says no : {joint['claimed_success_oracle_fail']}  <- FALSE SUCCESS")
    print(f"  claimed failure, oracle says yes: {joint['claimed_failure_oracle_pass']}")
    print(f"  claimed failure, oracle agrees  : {joint['claimed_failure_oracle_fail']}")
    print(
        f"  => {joint['claimed_success_oracle_fail']}/{claimed} "
        f"= {100 * joint['claimed_success_oracle_fail'] / max(claimed, 1):.1f}% of claimed successes are FALSE"
    )
    print(
        f"  => {joint['claimed_success_oracle_fail']}/{failures} "
        f"= {100 * joint['claimed_success_oracle_fail'] / max(failures, 1):.1f}% of failures were claimed as success"
    )

    print("\n-- the confound that decides the design (PREREGISTRATION §3) --")
    constant = [t for t, r in report["per_task_pass_rate"].items() if r in (0.0, 1.0)]
    print(f"  tasks with a CONSTANT label: {len(constant)} of {report['tasks_total']}")
    print(f"    {', '.join(constant)}")
    print(
        f"  askable tasks: {report['tasks_askable']}   items: {report['items_in_askable_tasks']}"
        f"   discordant pairs: {report['discordant_pairs']}"
    )
    print(f"  exact duplicate claim texts: {report['exact_duplicate_claims']}")

    print("\n-- within-task text is not degenerate --")
    by_task: dict[str, list[Solve]] = defaultdict(list)
    for solve in solves:
        by_task[solve.task].append(solve)
    for task in askable_tasks(solves)[:5]:
        texts = [s.claim[:800] for s in by_task[task]]
        sims = [
            difflib.SequenceMatcher(None, texts[i], texts[j]).ratio()
            for i in range(len(texts))
            for j in range(i + 1, len(texts))
        ]
        print(f"  {task:42s} pairwise similarity median={statistics.median(sims):.2f}")

    print(f"\n-- claim text: {report['claim_chars']}")


if __name__ == "__main__":
    instrument_check()
