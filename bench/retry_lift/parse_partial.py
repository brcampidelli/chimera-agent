"""Recompute retry-lift arm stats and paired deltas from a run's stdout log.

The runner only writes ``retry.json`` at the end, so a paused or orphaned run leaves nothing but its
stdout. This reads those per-solve lines back into the numbers the runner would have reported — how
run 1b's 255 salvaged solves stayed analysable after a session restart severed the process.

Usage:  python bench/retry_lift/parse_partial.py <log-file>
"""

from __future__ import annotations

import collections
import re
import sys

_ROW = re.compile(r"\s*\[\s*\d+/40\]\s+(\S+)\s+(\S+)\s+(PASS|FAIL)\s+att=(\S+)\s+inj=(\d+)/(\d+)")
_ARMS = ("control", "i1", "i2")


def parse(path: str) -> list[dict]:
    """Read the log into rows, tagging each with the seed it belongs to.

    Seeds are not printed per line, so they are reconstructed from position: the runner walks the
    40-task suite once per seed per arm, so every 40th row of an arm begins a new seed.
    """
    rows: list[dict] = []
    seen: collections.Counter = collections.Counter()
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = _ROW.match(line)
            if not match:
                continue
            arm, task_id, verdict, attempts, diff_inj, stall_inj = match.groups()
            seen[arm] += 1
            rows.append({
                "arm": arm,
                "id": task_id,
                "passed": verdict == "PASS",
                "attempts": int(attempts) if attempts.isdigit() else None,
                "diff_injections": int(diff_inj),
                "stall_injections": int(stall_inj),
                "seed": (seen[arm] - 1) // 40 + 1,
            })
    return rows


def _arm_line(rows: list[dict], arm: str) -> str | None:
    arm_rows = [r for r in rows if r["arm"] == arm]
    if not arm_rows:
        return None
    known = [r for r in arm_rows if r["attempts"] is not None]
    retried = [r for r in known if r["attempts"] >= 2]
    recovered = [r for r in retried if r["passed"]]
    passes = sum(1 for r in arm_rows if r["passed"])
    a_rate = len(retried) / len(known) if known else 0.0
    r_rate = len(recovered) / len(retried) if retried else 0.0
    return (
        f"  {arm:<8} n={len(arm_rows):>3}  pass {passes / len(arm_rows):6.1%}  "
        f"A {a_rate:6.1%}  R {r_rate:6.1%}   "
        f"inj: diff={sum(r['diff_injections'] for r in arm_rows)} "
        f"stall={sum(r['stall_injections'] for r in arm_rows)}"
    )


def _paired(rows: list[dict], treatment: str) -> list[str]:
    """Paired control-vs-treatment over the (seed, task) pairs both arms actually ran."""
    index = {(r["arm"], r["seed"], r["id"]): r for r in rows}
    control_keys = {(k[1], k[2]) for k in index if k[0] == "control"}
    treat_keys = {(k[1], k[2]) for k in index if k[0] == treatment}
    keys = sorted(control_keys & treat_keys)
    if not keys:
        return []
    ctrl_only = sum(
        1 for k in keys if index[("control", *k)]["passed"] and not index[(treatment, *k)]["passed"]
    )
    treat_only = sum(
        1 for k in keys if not index[("control", *k)]["passed"] and index[(treatment, *k)]["passed"]
    )
    out = [
        f"\n  {treatment.upper()} vs control (pareado, n={len(keys)}): "
        f"{treatment} +{treat_only} / control +{ctrl_only}  ->  "
        f"delta {(treat_only - ctrl_only) / len(keys):+.1%}"
    ]
    both = [
        k for k in keys
        if (index[("control", *k)]["attempts"] or 0) >= 2
        and (index[(treatment, *k)]["attempts"] or 0) >= 2
    ]
    if both:
        ctrl_pass = sum(1 for k in both if index[("control", *k)]["passed"])
        treat_pass = sum(1 for k in both if index[(treatment, *k)]["passed"])
        out.append(
            f"    lente de retry (n={len(both)}): control {ctrl_pass / len(both):.1%} "
            f"vs {treatment} {treat_pass / len(both):.1%}"
        )
    return out


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    rows = parse(sys.argv[1])
    print(f"progresso: {len(rows)}/360\n")
    for arm in _ARMS:
        if line := _arm_line(rows, arm):
            print(line)
    for arm in ("i1", "i2"):
        for line in _paired(rows, arm):
            print(line)


if __name__ == "__main__":
    main()
