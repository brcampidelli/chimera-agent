"""Score the arms of PREREGISTRATION §4 against the decision rule of §7. Spends nothing.

    python run.py                 # primary DV + secondary + both sabotage controls
    python run.py --stats-only    # write corpus_stats.json and stop

Every fitted arm is trained leave-one-task-out on the same population the DV evaluates, so the
held-out task's items are scored by a model that never saw that task.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import corpus
import detect
from corpus import Solve

BOOTSTRAP = 2000
SEED = 20260913

HERE = Path(__file__).resolve().parent


def within_task_auroc(
    scored: list[tuple[str, float, bool]],
) -> tuple[float, dict[str, tuple[float, int]]]:
    """Pooled concordance over (positive, negative) pairs that live inside the same task.

    `scored` is (task, score, is_positive) where positive = the thing we want ranked higher.
    Returns (pooled AUROC, per-task {task: (concordant, pairs)}) — the per-task pairs are what the
    cluster bootstrap resamples, because the pairs inside one task are not independent of it.
    """
    by_task: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for task, score, positive in scored:
        by_task[task].append((score, positive))

    per_task: dict[str, tuple[float, int]] = {}
    for task, rows in by_task.items():
        positives = [s for s, p in rows if p]
        negatives = [s for s, p in rows if not p]
        if not positives or not negatives:
            continue
        concordant = 0.0
        for high in positives:
            for low in negatives:
                if high > low:
                    concordant += 1.0
                elif high == low:
                    concordant += 0.5
        per_task[task] = (concordant, len(positives) * len(negatives))

    total_pairs = sum(n for _, n in per_task.values())
    pooled = sum(c for c, _ in per_task.values()) / total_pairs if total_pairs else float("nan")
    return pooled, per_task


def cluster_bootstrap(per_task: dict[str, tuple[float, int]], rng: random.Random) -> tuple[float, float]:
    """95% CI by resampling TASKS with replacement — the effective n is tasks, not pairs."""
    tasks = sorted(per_task)
    if len(tasks) < 2:
        return float("nan"), float("nan")
    draws = []
    for _ in range(BOOTSTRAP):
        picked = [per_task[rng.choice(tasks)] for _ in tasks]
        pairs = sum(n for _, n in picked)
        if pairs:
            draws.append(sum(c for c, _ in picked) / pairs)
    draws.sort()
    if not draws:
        return float("nan"), float("nan")
    return draws[int(0.025 * len(draws))], draws[min(int(0.975 * len(draws)), len(draws) - 1)]


def leave_one_task_out(
    arm_factory, population: list[Solve], positive_is_failure: bool = True
) -> list[tuple[str, float, bool]]:
    """Fit on every OTHER task, score the held-out one. Returns (task, score, is_positive)."""
    by_task: dict[str, list[Solve]] = defaultdict(list)
    for solve in population:
        by_task[solve.task].append(solve)

    scored: list[tuple[str, float, bool]] = []
    for held_out, rows in sorted(by_task.items()):
        training = [s for s in population if s.task != held_out]
        arm = arm_factory()
        arm.fit(training)
        for solve in rows:
            positive = (not solve.passed) if positive_is_failure else solve.passed
            scored.append((held_out, arm.score(solve), positive))
    return scored


def evaluate(arm_factory, population: list[Solve], rng: random.Random) -> dict:
    scored = leave_one_task_out(arm_factory, population)
    auroc, per_task = within_task_auroc(scored)
    low, high = cluster_bootstrap(per_task, rng)

    # A dead arm and a bad arm both print ~0.5, and they are not the same claim (Bee §2f). If an
    # arm assigns one single score to every item of every scored task it had nothing to read, and
    # saying "it scored 0.5" would report a measurement that never happened.
    by_task: dict[str, set[float]] = defaultdict(set)
    for task, score, _ in scored:
        if task in per_task:
            by_task[task].add(round(score, 12))
    degenerate = bool(by_task) and all(len(v) == 1 for v in by_task.values())

    return {
        "auroc": auroc,
        "ci95": [low, high],
        "tasks": len(per_task),
        "pairs": sum(n for _, n in per_task.values()),
        "degenerate": degenerate,
        "per_task": {t: round(c / n, 3) for t, (c, n) in sorted(per_task.items())},
    }


def naive_random_split(arm_factory, population: list[Solve], rng: random.Random) -> float:
    """The number that would be reported without the within-task discipline — the shortcut's size.

    Random 5-fold over ITEMS (so a task appears in both train and test), then AUROC pooled across
    tasks. This is the figure PREREGISTRATION §3 says is task identity rather than honesty.
    """
    items = list(population)
    rng.shuffle(items)
    folds = [items[i::5] for i in range(5)]
    scored: list[tuple[float, bool]] = []
    for index, fold in enumerate(folds):
        training = [s for j, f in enumerate(folds) if j != index for s in f]
        arm = arm_factory()
        arm.fit(training)
        scored.extend((arm.score(s), not s.passed) for s in fold)
    positives = [s for s, p in scored if p]
    negatives = [s for s, p in scored if not p]
    if not positives or not negatives:
        return float("nan")
    concordant = sum(
        1.0 if high > low else 0.5 if high == low else 0.0
        for high in positives
        for low in negatives
    )
    return concordant / (len(positives) * len(negatives))


def shuffled_within_task(population: list[Solve], rng: random.Random) -> list[Solve]:
    """Negative control: keep every claim, permute the labels INSIDE each task.

    Permuting within the task leaves the task's pass rate untouched, so an arm that still scores
    high is reading the label through something other than the text.
    """
    by_task: dict[str, list[Solve]] = defaultdict(list)
    for solve in population:
        by_task[solve.task].append(solve)
    out: list[Solve] = []
    for _, rows in sorted(by_task.items()):
        oracles = [s.oracle for s in rows]
        rng.shuffle(oracles)
        # `replace` rather than `Solve(**{**s.__dict__, ...})`. The dict form names THIS module's
        # `Solve` and therefore only works on this bench's rows; `bench/claim_vs_diff` reuses this
        # scorer on a richer row and got a TypeError for its trouble. The helpers in this file are
        # the shared ruler — one of them being secretly local to one corpus is the kind of thing
        # that makes two benches' numbers quietly incomparable.
        out.extend(replace(s, oracle=o) for s, o in zip(rows, oracles, strict=True))
    return out


def report(population: list[Solve], title: str, rng: random.Random) -> dict:
    print(f"\n=== {title} ===")
    print(f"    items {len(population)}")
    results = {}
    for arm in detect.arms() + detect.ceilings():
        factory = type(arm)
        outcome = evaluate(factory, population, rng)
        results[arm.name] = outcome
        low, high = outcome["ci95"]
        mark = "  <- CEILING, not a deployable number" if arm.name.endswith("_ceiling") else ""
        if outcome["degenerate"]:
            mark = "  <- DEGENERATE: one score for every item, nothing was read"
        print(
            f"  {arm.name:14s} AUROC {outcome['auroc']:.4f}  95% CI [{low:.3f}, {high:.3f}]"
            f"   tasks {outcome['tasks']}  pairs {outcome['pairs']}{mark}"
        )
    return results


def flag_rate_table(population: list[Solve], rng: random.Random) -> dict:
    """Catch at equal flag rate, ranked WITHIN task.

    Ranking within task hands the detector a per-task calibration production would not have, so
    these are upper bounds (§2r: a measurement made where the intervention cannot misfire estimates
    the gain, never the damage).
    """
    print("\n=== catch at equal flag rate (within-task ranking — an UPPER bound) ===")
    table: dict = {}
    for arm in detect.arms() + detect.ceilings():
        scored = leave_one_task_out(type(arm), population)
        by_task: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        for task, score, positive in scored:
            by_task[task].append((score, positive))
        row = {}
        for budget in (0.10, 0.20, 0.30):
            caught = total = 0
            for rows in by_task.values():
                ordered = sorted(rows, key=lambda r: -r[0])
                take = max(1, round(budget * len(ordered)))
                caught += sum(1 for _, positive in ordered[:take] if positive)
                total += sum(1 for _, positive in rows if positive)
            row[f"{int(budget * 100)}%"] = f"{caught}/{total}"
        table[arm.name] = row
        print(f"  {arm.name:14s} " + "   ".join(f"flag {k}: caught {v}" for k, v in row.items()))
    return table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args()

    solves, dropped = corpus.load()
    stats = corpus.stats(solves, dropped)
    (HERE / "corpus_stats.json").write_text(
        json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote corpus_stats.json — {stats['solves_usable']} usable solves")
    if args.stats_only:
        return

    rng = random.Random(SEED)

    print("\n### positive control (PREREGISTRATION §8) — must be exactly 1.000")
    claimed = [s for s in solves if s.self_report]
    control, _ = within_task_auroc(
        [(s.task, detect.Oracle().score(s), not s.passed) for s in claimed]
    )
    print(f"    oracle_control AUROC {control:.3f}")
    if abs(control - 1.0) > 1e-9:
        raise SystemExit("HALT: the scorer cannot rank a perfect predictor at 1.000 — it is broken")

    primary = report(claimed, "PRIMARY — claimed successes, true vs false, within task", rng)
    secondary = report(solves, "SECONDARY — all solves, pass vs fail, within task", rng)

    print("\n=== the shortcut: same arms and same hyper-parameters, naive random split (§3) ===")
    shortcut: dict[str, dict[str, float]] = {}
    for label, population, within in (
        ("primary population", claimed, primary),
        ("secondary population", solves, secondary),
    ):
        print(f"  -- {label} --")
        for arm in detect.arms():
            if arm.name == "self_report":
                continue
            value = naive_random_split(type(arm), population, random.Random(SEED))
            shortcut.setdefault(arm.name, {})[label] = value
            print(
                f"     {arm.name:12s} random-split {value:.4f}"
                f"   within-task {within[arm.name]['auroc']:.4f}"
                f"   gap {value - within[arm.name]['auroc']:+.4f}"
            )

    print("\n=== negative control (§8) — labels shuffled within task, must be ~0.500 ===")
    sabotage = {}
    shuffled = shuffled_within_task(claimed, random.Random(SEED))
    for arm in detect.arms() + detect.ceilings():
        outcome = evaluate(type(arm), shuffled, random.Random(SEED))
        sabotage[arm.name] = outcome["auroc"]
        print(f"  {arm.name:14s} AUROC {outcome['auroc']:.4f}")

    flags = flag_rate_table(claimed, rng)

    print("\n=== decision rule (§7), read on the PRIMARY ===")
    for name in ("tfidf", "harness"):
        row = primary[name]
        gate_a = row["auroc"] >= 0.75 and row["ci95"][0] >= 0.65
        gate_b = row["auroc"] - primary["length"]["auroc"] >= 0.05
        gate_c = row["auroc"] - primary["self_report"]["auroc"] >= 0.10
        print(
            f"  {name:8s} auroc {row['auroc']:.4f} | >=0.75 and CI low >=0.65: {gate_a}"
            f" | beats length by >=0.05: {gate_b}"
            f" | beats self_report by >=0.10: {gate_c}"
            f"  => {'ADOPT' if (gate_a and gate_b and gate_c) else 'DO NOT BUILD'}"
            + ("  (and it is DEGENERATE — the rule was never tested on it)" if row["degenerate"] else "")
        )

    (HERE / "results.json").write_text(
        json.dumps(
            {
                "primary": primary,
                "secondary": secondary,
                "naive_random_split": shortcut,
                "sabotage_shuffled_within_task": sabotage,
                "catch_at_flag_rate": flags,
                "positive_control": control,
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
