"""Read the grid against the pre-registered metrics and decision rule. Nothing else.

    python bench/cache_confound/read.py ~/cache-confound/grid.jsonl

M0 first-request identity per cell (Amendment 2 — a cell whose replicas did not start from the same
bytes is void) · M1 cache hit rate by condition (the intervention check) · M2 trajectory identity
per cell (primary) · M3 within-cell SD of the grader's score (secondary) · M4 the provider gate. Then
the decision
table from `PREREGISTRATION.md`, applied mechanically — the reading is decided by the rule that was
written before the numbers, not by whoever runs this.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def main(path: str) -> int:
    rows = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    pins = {r.get("pin") for r in rows}
    pin = next(iter(pins)) if len(pins) == 1 else None

    # M4 — the provider gate: a solve another route served is not an observation of the pinned one.
    kept, dropped = [], []
    for r in rows:
        (kept if (not pin or r.get("provider") == pin) else dropped).append(r)
    print(f"rows: {len(rows)}   pin: {pin!r}   kept: {len(kept)}   dropped (other provider): {len(dropped)}")
    if dropped:
        for r in dropped:
            print(f"   dropped {r['task']}-{r['condition']}-r{r['replica']}: provider={r.get('provider')!r}")

    # M0 (Amendment 2) — were the replicas byte-identical at their first request? Read from the
    # artefact, per (task, condition) cell: the task-text hash and the route's step-1 token count.
    # A cell that disagrees on either is void and is said so before any of its numbers is read.
    print("\nM0 — first-request identity per cell (task_sha and step-1 prompt_tokens across replicas)")
    void: set[tuple[str, str]] = set()
    by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in kept:
        by_cell[(r["task"], r["condition"])].append(r)
    for (task, cond), members in sorted(by_cell.items()):
        shas = {m.get("task_sha") for m in members}
        toks = {m.get("step1_prompt_tokens") for m in members}
        step1 = [f"{m.get('step1_cached_tokens')}/{m.get('step1_prompt_tokens')}" for m in members]
        if None in shas or None in toks:
            print(f"  {task:<40} {cond:<7} k={len(members)}  NOT RECORDED (pre-Amendment-2 rows)  step1={step1}")
            void.add((task, cond))
        elif len(shas) == 1 and len(toks) == 1:
            print(f"  {task:<40} {cond:<7} k={len(members)}  IDENTICAL  sha={next(iter(shas))} "
                  f"prompt_tokens={next(iter(toks))}  step1 cached/prompt={step1}")
        else:
            print(f"  {task:<40} {cond:<7} k={len(members)}  VOID — shas={sorted(map(str, shas))} "
                  f"prompt_tokens={sorted(map(str, toks))}")
            void.add((task, cond))

    # M1 — did the intervention act?
    print("\nM1 — cache hit rate (cache_read_tokens / prompt_tokens), by condition")
    for cond in ("SHARED", "NONCE"):
        rates = []
        silent = 0
        for r in kept:
            if r["condition"] != cond:
                continue
            c, p = r.get("cache_read_tokens"), r.get("prompt_tokens") or 0
            if c is None:
                silent += 1
            elif p > 0:
                rates.append(c / p)
        if rates:
            print(f"  {cond:<7} n={len(rates):<3} mean={statistics.fmean(rates):.3f}  "
                  f"min={min(rates):.3f}  max={max(rates):.3f}  silent(None)={silent}")
        else:
            print(f"  {cond:<7} n=0   silent(None)={silent}  <- the route reported nothing; M1 cannot be read")

    # M2 — trajectory identity per (task, condition) cell.
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in kept:
        cells[(r["task"], r["condition"])].append(r)
    print("\nM2 — trajectory identity per cell (same ordered tool_names AND same fingerprint across replicas)")
    identical = {"SHARED": 0, "NONCE": 0}
    counted = {"SHARED": 0, "NONCE": 0}
    for (task, cond), members in sorted(cells.items()):
        if len(members) < 2:
            print(f"  {task:<40} {cond:<7} k={len(members)}  (one replica — not a cell)")
            continue
        if (task, cond) in void:
            print(f"  {task:<40} {cond:<7} k={len(members)}  VOID at M0 — not counted")
            continue
        trajs = {(tuple(m.get("tool_names") or []), m.get("fingerprint")) for m in members}
        tools_only = {tuple(m.get("tool_names") or []) for m in members}
        same = len(trajs) == 1
        counted[cond] += 1
        identical[cond] += int(same)
        print(f"  {task:<40} {cond:<7} k={len(members)}  distinct trajectories={len(trajs)}  "
              f"distinct tool sequences={len(tools_only)}  {'IDENTICAL' if same else 'diverged'}")
    print(f"\n  identical cells: SHARED {identical['SHARED']}/{counted['SHARED']}   "
          f"NONCE {identical['NONCE']}/{counted['NONCE']}")

    # M3 — outcome flip rate.
    print("\nM3 — within-cell SD of outcome_score")
    for (task, cond), members in sorted(cells.items()):
        scores = [m["outcome_score"] for m in members if isinstance(m.get("outcome_score"), (int, float))]
        sd = statistics.pstdev(scores) if len(scores) >= 2 else float("nan")
        print(f"  {task:<40} {cond:<7} scores={[round(s, 3) for s in scores]}  sd={sd:.3f}")

    # The decision rule, verbatim from the registration.
    n_cells = counted["NONCE"]
    nonce_id, shared_id = identical["NONCE"], identical["SHARED"]
    print("\nDECISION (pre-registered)")
    if n_cells == 0:
        print("  no complete NONCE cells — nothing to decide")
        return 0
    if nonce_id <= 2 and n_cells == 4:
        print("  NONCE identical in <= 2 of 4 cells: hosted nondeterminism beyond the cache. The paper's")
        print("  dichotomy does not transfer to this route. CLOSED as not isolable here; the T=0 floor stands.")
    elif shared_id >= nonce_id - 1:
        print("  NONCE identical in >= 3 cells and SHARED within one cell of it: the cache adds nothing")
        print("  detectable at T=0 on this route. CLOSED; the archive stands.")
    else:
        print("  NONCE identical in >= 3 cells and SHARED at least two cells behind: the effect CROSSES the")
        print("  hosted API. REOPEN: register the T=0.2 follow-up; annotate every published paired RESULTS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "cache-confound" / "grid.jsonl")))
