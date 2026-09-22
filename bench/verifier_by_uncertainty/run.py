"""The strong verifier fired by uncertainty: which claimed successes would be sent to D9, and what
that buys against the shipped `index > 1` trigger and against flagging at random. Spends nothing.

    python run.py            # from bench/verifier_by_uncertainty, in the env that holds the corpus

Everything is imported from the two benches this one sits on — the corpus and the arms from
`bench/claim_vs_diff`, the leave-one-task-out scorer from `bench/false_success` — so the counts here
are the counts published there (PREREGISTRATION §7, the paired control), not a second ruler.
"""

from __future__ import annotations

import glob
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
for sibling in ("claim_vs_diff", "false_success"):
    path = str(HERE.parent / sibling)
    if path not in sys.path:
        sys.path.insert(0, path)

import claims  # noqa: E402
import evidence  # noqa: E402
from run import leave_one_task_out, within_task_auroc  # noqa: E402

SEED = 20260913
DRAWS = 2000
BUDGETS = (0.10, 0.20, 0.30)
PUBLISHED_OVERLAP = {"10%": 13, "20%": 30, "30%": 48}
"""`bench/claim_vs_diff/results.json` -> catch_at_flag_rate.overlap. The paired control."""


# --- the shipped trigger, off the receipts ---------------------------------------------------------


def attempts_of(homes: Path) -> dict[tuple[str, str], int]:
    """How many attempts the LAST run of each solve recorded; `index > 1` is `attempts > 1`."""
    out: dict[tuple[str, str], int] = {}
    for directory in sorted(glob.glob(str(homes / "*"))):
        match = claims._HID.match(os.path.basename(directory))
        receipt = Path(directory) / "runs.jsonl"
        if not match or not receipt.is_file():
            continue
        lines = [
            json.loads(raw) for raw in receipt.read_text(encoding="utf-8").splitlines() if raw.strip()
        ]
        if lines:
            out[(match.group("task"), match.group("hid"))] = len(lines[-1].get("attempts") or [])
    return out


# --- ranking within task ---------------------------------------------------------------------------

Groups = dict[str, list[tuple[float, bool]]]


def by_task(scored: list[tuple[str, float, bool]]) -> Groups:
    groups: Groups = defaultdict(list)
    for task, score, positive in scored:
        groups[task].append((score, positive))
    return groups


def take_of(budget: float, n: int) -> int:
    return max(1, round(budget * n))


def catch_low(groups: Groups, budget: float) -> tuple[int, int]:
    """Fire on the HIGHEST scores (the arm scores P(false), so high = most suspicious). Calls too."""
    caught = calls = 0
    for rows in groups.values():
        ordered = sorted(rows, key=lambda r: -r[0])
        take = take_of(budget, len(ordered))
        caught += sum(1 for _, positive in ordered[:take] if positive)
        calls += take
    return caught, calls


def catch_band(groups: Groups, budget: float) -> tuple[int, int]:
    """Fire on the MIDDLE of the ranking: the items from the b/2 quantile down, the same number of
    calls as `catch_low` at the same budget."""
    caught = calls = 0
    for rows in groups.values():
        ordered = sorted(rows, key=lambda r: -r[0])
        take = take_of(budget, len(ordered))
        start = max(0, round(budget * len(ordered) / 2))
        window = ordered[start : start + take]
        caught += sum(1 for _, positive in window if positive)
        calls += len(window)
    return caught, calls


def catch_random(groups: Groups, budget: float) -> float:
    """The EXPECTATION of flagging take_t of n_t uniformly: sum over tasks of take_t * pos_t / n_t."""
    return sum(
        take_of(budget, len(rows)) * sum(1 for _, p in rows if p) / len(rows)
        for rows in groups.values()
    )


def positives(groups: Groups) -> int:
    return sum(1 for rows in groups.values() for _, p in rows if p)


# --- uncertainty -----------------------------------------------------------------------------------


def bootstrap_extra(groups: Groups, budget: float, rng: random.Random) -> tuple[float, float]:
    """95% CI of catch_low - random, resampling TASKS with replacement."""
    tasks = sorted(groups)
    draws = []
    for _ in range(DRAWS):
        picked = {f"{t}#{i}": groups[t] for i, t in enumerate(rng.choice(tasks) for _ in tasks)}
        low, _ = catch_low(picked, budget)
        draws.append(low - catch_random(picked, budget))
    draws.sort()
    return draws[int(0.025 * DRAWS)], draws[min(int(0.975 * DRAWS), DRAWS - 1)]


def permutation_p(
    groups: Groups, budget: float, observed: int, rng: random.Random
) -> tuple[float, float]:
    """Labels shuffled WITHIN task: the null of catch_low; p = P(null >= observed).
    Returns (p, mean of null catch_low - random)."""
    at_least = 0
    extra = 0.0
    for _ in range(DRAWS):
        shuffled: Groups = {}
        for task, rows in groups.items():
            labels = [p for _, p in rows]
            rng.shuffle(labels)
            shuffled[task] = [(s, lab) for (s, _), lab in zip(rows, labels, strict=True)]
        low, _ = catch_low(shuffled, budget)
        at_least += low >= observed
        extra += low - catch_random(shuffled, budget)
    return at_least / DRAWS, extra / DRAWS


# --- main ------------------------------------------------------------------------------------------


def main() -> None:
    solves, dropped = claims.load()
    claimed = [s for s in solves if s.self_report]
    false = sum(1 for s in claimed if not s.passed)
    print(f"solves {len(solves)}  dropped {dropped}  claimed {len(claimed)}  false {false}")

    # Q0 -- the shipped trigger.
    attempts = attempts_of(Path(os.path.expanduser("~/hb-homes")))
    shipped = [s for s in claimed if attempts.get((s.task, s.hid), 1) > 1]
    shipped_catch = sum(1 for s in shipped if not s.passed)
    print("\n=== Q0 -- the shipped trigger (index > 1) on this corpus ===")
    print(
        f"  claimed successes at attempt >= 2: {len(shipped)}/{len(claimed)}"
        f"  -> D9 calls {len(shipped)}, catch {shipped_catch}/{false}"
    )

    # positive control -- the ruler still ranks a perfect predictor at 1.000
    control, _ = within_task_auroc(
        [(s.task, evidence.OracleControl().score(s), not s.passed) for s in claimed]
    )
    if abs(control - 1.0) > 1e-9:
        raise SystemExit(f"HALT: positive control {control:.4f} != 1.000")

    scored = leave_one_task_out(evidence.Overlap, claimed)
    groups = by_task(scored)
    oracle_groups = by_task(
        [(s.task, evidence.OracleControl().score(s), not s.passed) for s in claimed]
    )
    assert positives(groups) == false

    print("\n=== catch at equal budget, ranked within task, leave-one-task-out ===")
    print(
        f"  {'budget':6s} {'calls':>5s}  {'overlap_low':>11s}  {'overlap_band':>12s}  {'random':>7s}"
        f"  {'oracle':>6s}   {'extra':>6s}  calls/extra   95% CI (tasks)   perm p   null extra"
    )
    out: dict[str, dict] = {}
    for budget in BUDGETS:
        key = f"{int(budget * 100)}%"
        low, calls = catch_low(groups, budget)
        band, band_calls = catch_band(groups, budget)
        rnd = catch_random(groups, budget)
        ceiling, _ = catch_low(oracle_groups, budget)
        extra = low - rnd
        ci = bootstrap_extra(groups, budget, random.Random(SEED))
        perm_p, null_extra = permutation_p(groups, budget, low, random.Random(SEED + 1))
        per = (calls / extra) if extra > 0 else float("inf")
        print(
            f"  {key:6s} {calls:5d}  {low:6d}/{false}   {band:6d}/{false}  {rnd:7.1f}"
            f"  {ceiling:3d}/{false}   {extra:+6.1f}  {per:11.1f}   [{ci[0]:+.1f}, {ci[1]:+.1f}]"
            f"   {perm_p:.3f}   {null_extra:+.2f}"
        )
        if low != PUBLISHED_OVERLAP[key]:
            raise SystemExit(
                f"HALT: paired control -- overlap at {key} is {low}, published {PUBLISHED_OVERLAP[key]}"
            )
        out[key] = {
            "calls": calls,
            "overlap_low": low,
            "overlap_band": band,
            "band_calls": band_calls,
            "random_expected": round(rnd, 2),
            "oracle_ceiling": ceiling,
            "extra": round(extra, 2),
            "calls_per_extra": (round(per, 1) if extra > 0 else None),
            "ci95_extra": [round(ci[0], 2), round(ci[1], 2)],
            "permutation_p": perm_p,
            "null_extra_mean": round(null_extra, 3),
        }

    print("\n=== decision rule (PREREGISTRATION section 6), at 30% ===")
    r = out["30%"]
    ci_excludes_zero = r["ci95_extra"][0] > 0 and r["permutation_p"] <= 0.05
    cheap_enough = r["calls_per_extra"] is not None and r["calls_per_extra"] <= 10
    verdict = "ADOPT" if (ci_excludes_zero and cheap_enough) else "NULL -- D9 stays as it is"
    print(
        f"  CI excludes 0 and perm p <= 0.05: {ci_excludes_zero}"
        f"   calls per extra catch <= 10: {cheap_enough}   => {verdict}"
    )
    predictions = {
        "P0_shipped_covers_zero": len(shipped) == 0,
        "P1_ci_includes_zero_at_30": not (r["ci95_extra"][0] > 0),
        "P2_band_below_low_every_budget": all(
            out[k]["overlap_band"] < out[k]["overlap_low"] for k in out
        ),
        "P3_null": verdict.startswith("NULL"),
    }
    print("  predictions:", predictions)

    (HERE / "results.json").write_text(
        json.dumps(
            {
                "corpus": {
                    "solves": len(solves),
                    "claimed": len(claimed),
                    "false": false,
                    "dropped": dropped,
                },
                "shipped_trigger": {
                    "covers": len(shipped),
                    "calls": len(shipped),
                    "catch": shipped_catch,
                },
                "positive_control_auroc": control,
                "catch": out,
                "verdict": verdict,
                "predictions": predictions,
                "seed": SEED,
                "draws": DRAWS,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("\nwrote results.json")


if __name__ == "__main__":
    main()
