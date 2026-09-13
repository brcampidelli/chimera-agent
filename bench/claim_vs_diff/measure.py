"""Score the arms of PREREGISTRATION §5 against the decision rule of §8. Spends nothing.

    python measure.py

The scorer is IMPORTED from `bench/false_success/run.py` — `within_task_auroc`, the cluster
bootstrap over tasks, the leave-one-task-out fit and the within-task label shuffle. Reusing it is
what makes the number here comparable to the 0.5996 published there without an argument about
whether the two rulers agree: there is one ruler.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import claims
import evidence

_SIBLING = Path(__file__).resolve().parent.parent / "false_success"
if str(_SIBLING) not in sys.path:
    sys.path.insert(0, str(_SIBLING))

from run import (  # noqa: E402  (path set immediately above)
    evaluate,
    leave_one_task_out,
    naive_random_split,
    shuffled_within_task,
    within_task_auroc,
)

HERE = Path(__file__).resolve().parent
SEED = 20260913
PUBLISHED_TFIDF = 0.5996
"""`bench/false_success/RESULTS.md`, primary DV. The third control of §9."""


def report(population: list, title: str, rng: random.Random) -> dict:
    print(f"\n=== {title} ===")
    print(f"    items {len(population)}")
    results = {}
    for arm in evidence.arms():
        outcome = evaluate(type(arm), population, rng)
        results[arm.name] = outcome
        low, high = outcome["ci95"]
        mark = "  <- DEGENERATE: one score for every item" if outcome["degenerate"] else ""
        print(
            f"  {arm.name:12s} AUROC {outcome['auroc']:.4f}  95% CI [{low:.3f}, {high:.3f}]"
            f"   tasks {outcome['tasks']}  pairs {outcome['pairs']}{mark}"
        )
    return results


def main() -> None:
    solves, dropped = claims.load()
    stats = claims.stats(solves, dropped)
    (HERE / "corpus_stats.json").write_text(
        json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote corpus_stats.json — {stats['solves_usable']} usable solves")

    rng = random.Random(SEED)
    claimed = [s for s in solves if s.self_report]

    print("\n### control 1 (positive, §9) — must be exactly 1.000")
    control, _ = within_task_auroc(
        [(s.task, evidence.OracleControl().score(s), not s.passed) for s in claimed]
    )
    print(f"    oracle_control {control:.4f}")
    if abs(control - 1.0) > 1e-9:
        raise SystemExit("HALT: the scorer cannot rank a perfect predictor at 1.000")

    primary = report(claimed, "PRIMARY — claimed successes, true vs false, within task", rng)

    print("\n### control 3 (paired, §1 and §9) — the published baseline must come back")
    delta = abs(primary["claim_tfidf"]["auroc"] - PUBLISHED_TFIDF)
    print(
        f"    claim_tfidf {primary['claim_tfidf']['auroc']:.4f}"
        f"   published {PUBLISHED_TFIDF:.4f}   |delta| {delta:.4f}"
    )
    if delta > 0.001:
        raise SystemExit(
            "HALT: the baseline did not reproduce. The apparatus changed between #457 and this run, "
            "so nothing else here is a comparison."
        )

    print("\n=== the shortcut: same arms, naive random split (PROTOCOL §7) ===")
    shortcut = {}
    for arm in evidence.arms():
        value = naive_random_split(type(arm), claimed, random.Random(SEED))
        shortcut[arm.name] = value
        print(
            f"  {arm.name:12s} random-split {value:.4f}"
            f"   within-task {primary[arm.name]['auroc']:.4f}"
            f"   gap {value - primary[arm.name]['auroc']:+.4f}"
        )

    print("\n### control 2 (negative, §9) — labels shuffled within task, must be ~0.500")
    sabotage = {}
    shuffled = shuffled_within_task(claimed, random.Random(SEED))
    for arm in evidence.arms():
        outcome = evaluate(type(arm), shuffled, random.Random(SEED))
        sabotage[arm.name] = outcome["auroc"]
        print(f"  {arm.name:12s} {outcome['auroc']:.4f}")

    print("\n=== the rule (§5), as counts — never an AUROC ===")
    rule = evidence.contradiction_report(solves)
    print(f"  {rule}")

    print("\n=== catch at equal flag rate, ranked within task (an UPPER bound) ===")
    flags: dict = {}
    for arm in evidence.arms():
        scored = leave_one_task_out(type(arm), claimed)
        by_task: dict[str, list[tuple[float, bool]]] = {}
        for task, score, positive in scored:
            by_task.setdefault(task, []).append((score, positive))
        row = {}
        for budget in (0.10, 0.20, 0.30):
            caught = total = 0
            for rows in by_task.values():
                ordered = sorted(rows, key=lambda r: -r[0])
                take = max(1, round(budget * len(ordered)))
                caught += sum(1 for _, positive in ordered[:take] if positive)
                total += sum(1 for _, positive in rows if positive)
            row[f"{int(budget * 100)}%"] = f"{caught}/{total}"
        flags[arm.name] = row
        print(f"  {arm.name:12s} " + "   ".join(f"flag {k}: {v}" for k, v in row.items()))

    print("\n=== decision rule (§8) ===")
    for name in ("evidence", "combined"):
        row = primary[name]
        gate_a = row["auroc"] >= 0.70 and row["ci95"][0] >= 0.60
        gate_b = row["auroc"] - primary["claim_tfidf"]["auroc"] >= 0.10
        print(
            f"  {name:9s} auroc {row['auroc']:.4f} | >=0.70 and CI low >=0.60: {gate_a}"
            f" | beats claim_tfidf by >=0.10: {gate_b}"
            f"  => {'ADOPT' if (gate_a and gate_b) else 'DO NOT BUILD'}"
        )
    rule_ok = (rule["precision"] or 0) >= 0.80 and rule["fired"] >= 15
    print(
        f"  rule      fired {rule['fired']} precision {rule['precision']}"
        f"  => {'ADOPT THE RULE' if rule_ok else 'DO NOT SHIP THE RULE'}"
    )

    (HERE / "results.json").write_text(
        json.dumps(
            {
                "primary": primary,
                "naive_random_split": shortcut,
                "sabotage_shuffled_within_task": sabotage,
                "contradiction_rule": rule,
                "catch_at_flag_rate": flags,
                "positive_control": control,
                "baseline_reproduced": primary["claim_tfidf"]["auroc"],
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
