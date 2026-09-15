"""#453's three main effects, read within the two partitions `PREREGISTRATION-partitions.md` declares.

    ~/hb-venv/bin/python bench/harness_bench/effects_by_partition.py

Same per-task deltas as `read_results.py`, the 087 correction applied first (`effects_with_087_corrected`),
then: the effect within each stratum with a bootstrap CI over that stratum's tasks, the interaction
between strata with a CI that resamples each stratum independently, and the within-cell noise SD per
stratum. The family partition is the benchmark's own `task.yaml: class`; the tercile partition is the
bare arm's mean, rule fixed before this file ran. Nothing is written back into the harness's files.
"""

from __future__ import annotations

import random
import re
import statistics
import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from effects_with_087_corrected import BITS, HOME, TASKS, cells  # noqa: E402

SE = "Software Engineering & Codebase Maintenance"
SEED = 20260915


def families() -> dict[str, str]:
    out = {}
    for task in TASKS:
        text = (HOME / "tasks" / task / "task.yaml").read_text(encoding="utf-8")
        found = re.search(r'^class:\s*"?([^"\n]+)"?', text, re.M)
        out[task] = found.group(1).strip() if found else "?"
    return out


def per_task_deltas(grid: dict) -> dict[str, dict[str, float]]:
    """{factor: {task: delta}} — the same quantity `read_results.py` averages."""
    per_task_arm: dict = {}
    for (task, bits, _k), value in grid.items():
        per_task_arm.setdefault((task, bits), []).append(value)
    mean = {key: statistics.fmean(vs) for key, vs in per_task_arm.items()}
    out: dict[str, dict[str, float]] = {}
    for index, name in ((0, "A repo-map"), (1, "B checklist"), (2, "C planner")):
        out[name] = {}
        for task in TASKS:
            withs = [mean[(task, b)] for b in BITS if b[index] == 1 and (task, b) in mean]
            withouts = [mean[(task, b)] for b in BITS if b[index] == 0 and (task, b) in mean]
            if withs and withouts:
                out[name][task] = statistics.fmean(withs) - statistics.fmean(withouts)
    return out


def bare_means(grid: dict) -> dict[str, float]:
    per_task: dict[str, list[float]] = {}
    for (task, bits, _k), value in grid.items():
        if bits == (0, 0, 0):
            per_task.setdefault(task, []).append(value)
    return {t: statistics.fmean(v) for t, v in per_task.items()}


def within_cell_sd(grid: dict, tasks: Iterable[str]) -> float:
    """Pooled SD of replicas within (task, arm) cells, over the given tasks — the noise floor."""
    tasks = set(tasks)
    per_cell: dict = {}
    for (task, bits, _k), value in grid.items():
        if task in tasks:
            per_cell.setdefault((task, bits), []).append(value)
    variances = [statistics.pvariance(vs) for vs in per_cell.values() if len(vs) >= 2]
    return statistics.fmean(variances) ** 0.5 if variances else float("nan")


def boot_mean(xs: list[float], rng: random.Random, n: int = 10000) -> tuple[float, float]:
    draws = sorted(statistics.fmean(rng.choice(xs) for _ in xs) for _ in range(n))
    return draws[int(n * 0.025)], draws[int(n * 0.975)]


def boot_diff(a: list[float], b: list[float], rng: random.Random, n: int = 10000,
              alpha: float = 0.05) -> tuple[float, float]:
    draws = sorted(
        statistics.fmean(rng.choice(a) for _ in a) - statistics.fmean(rng.choice(b) for _ in b)
        for _ in range(n)
    )
    return draws[int(n * alpha / 2)], draws[int(n * (1 - alpha / 2))]


#: Six interactions are read (3 factors x 2 partitions). The registration did not say how to
#: correct for that, which is its defect; `chimera/eval/anytime.py` and study 10 already settled on
#: Bonferroni across decisions, so the corrected interval is printed beside the registered one and
#: the RESULTS say which of the two the reading follows.
INTERACTIONS_READ = 6


def report(name: str, strata: dict[str, list[str]], deltas: dict[str, dict[str, float]], grid: dict) -> None:
    print(f"\n== {name} ==")
    for label, tasks in strata.items():
        print(f"  {label:<12} n={len(tasks):<2} within-cell SD {within_cell_sd(grid, tasks):.3f}")
    labels = list(strata)
    for factor, by_task in deltas.items():
        rng = random.Random(SEED)
        print(f"  {factor}")
        means = {}
        for label in labels:
            xs = [by_task[t] for t in strata[label] if t in by_task]
            low, high = boot_mean(xs, rng)
            means[label] = xs
            flag = "" if low < 0 < high else "  <-- excludes zero"
            print(f"     {label:<12} {statistics.fmean(xs):+.3f} [{low:+.3f}, {high:+.3f}]  n={len(xs)}{flag}")
        first, last = labels[0], labels[-1]
        low, high = boot_diff(means[first], means[last], rng)
        diff = statistics.fmean(means[first]) - statistics.fmean(means[last])
        flag = "" if low < 0 < high else "  <-- excludes zero"
        print(f"     interaction {first} − {last}: {diff:+.3f} [{low:+.3f}, {high:+.3f}]{flag}")
        rng2 = random.Random(SEED)
        blow, bhigh = boot_diff(means[first], means[last], rng2, alpha=0.05 / INTERACTIONS_READ)
        bflag = "" if blow < 0 < bhigh else "  <-- excludes zero even corrected"
        print(f"       Bonferroni over {INTERACTIONS_READ}:  [{blow:+.3f}, {bhigh:+.3f}]{bflag}")


def main() -> None:
    grid = cells(corrected=True)
    deltas = per_task_deltas(grid)
    fam = families()
    # Same method as the strata below, over all 23 tasks — so the strata are compared to an
    # aggregate computed the same way, not to the figure `read_results.py` printed by its own.
    print(f"within-cell SD, all 23 tasks, this method: {within_cell_sd(grid, TASKS):.3f}")

    se = [t for t in TASKS if fam[t] == SE]
    other = [t for t in TASKS if fam[t] != SE]
    print("family partition (task.yaml: class):")
    for t in TASKS:
        print(f"  {t:<42} {fam[t]}")
    report("FAMILY: SE (16) vs other (7)", {"SE": se, "other": other}, deltas, grid)

    bare = bare_means(grid)
    ordered = sorted(TASKS, key=lambda t: bare[t])
    bottom, middle, top = ordered[:8], ordered[8:16], ordered[16:]
    print("\ntercile partition (bare-arm mean, ascending):")
    for label, tasks in (("bottom", bottom), ("middle", middle), ("top", top)):
        print(f"  {label:<7} " + ", ".join(f"{t.split('-')[0]}={bare[t]:.2f}" for t in tasks))
    print("\n  cross-tabulation family × tercile (the confound the registration said to print):")
    for label, tasks in (("bottom", bottom), ("middle", middle), ("top", top)):
        n_se = sum(1 for t in tasks if fam[t] == SE)
        print(f"     {label:<7} SE {n_se}  other {len(tasks) - n_se}")
    report("TERCILE: bottom (8) vs top (7)", {"bottom": bottom, "middle": middle, "top": top}, deltas, grid)


if __name__ == "__main__":
    main()
