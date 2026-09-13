"""Read the 2PL fit, and ask the two questions it was fitted for. Spends nothing.

    python bench/irt/run.py

1. How many of the factorial's 23 tasks could have distinguished ANY arm from any other?
2. Does ranking the eight arms by IRT ability agree with ranking them by aggregate pass rate —
   the displacement arXiv 2609.09372 puts at ~22%?
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict

from fit import ARMS, HERE, REPLICAS, TASKS, fit, items, matrix

SEED = 20260913


def _rank(values: dict[str, float]) -> list[str]:
    return [k for k, _ in sorted(values.items(), key=lambda kv: -kv[1])]


def main() -> None:
    names, grid = matrix()
    fitted = items(grid)
    ability, _disc, _diff, informative = fit(grid)

    dead = [i for i in fitted if not i.informative]
    live = [i for i in fitted if i.informative]

    print(f"respondents {len(names)}  items {len(TASKS)}")
    print(f"\n-- items that could not distinguish ANY respondent from any other: {len(dead)} of {len(TASKS)}")
    for item in dead:
        print(f"   {item.task:42s} everyone scored {item.rate:.0f}")

    print(f"\n-- the {len(live)} that could, by fitted discrimination (the ordering, not the value) --")
    print(f"   {'task':42s} {'rate':>5s} {'discrim':>8s} {'difficulty':>10s}")
    for item in sorted(live, key=lambda i: -i.discrimination):
        print(f"   {item.task:42s} {item.rate:>5.2f} {item.discrimination:>8.2f} {item.difficulty:>10.2f}")

    # Arm ranking: aggregate pass rate against mean fitted ability.
    by_arm_rate: dict[str, list[float]] = defaultdict(list)
    by_arm_theta: dict[str, list[float]] = defaultdict(list)
    for index, name in enumerate(names):
        arm = name.rsplit("-r", 1)[0]
        answered = [c for c in grid[index] if c is not None]
        by_arm_rate[arm].append(sum(1 for c in answered if c) / len(answered))
        by_arm_theta[arm].append(ability[index])
    rate_mean = {a: statistics.fmean(v) for a, v in by_arm_rate.items()}
    theta_mean = {a: statistics.fmean(v) for a, v in by_arm_theta.items()}

    rank_rate, rank_theta = _rank(rate_mean), _rank(theta_mean)
    moved = sum(1 for a in ARMS if rank_rate.index(a) != rank_theta.index(a))
    print("\n-- the eight arms, ranked two ways --")
    print(f"   {'#':>2} {'by aggregate rate':>20} {'by fitted ability':>20}")
    for position, (a, b) in enumerate(zip(rank_rate, rank_theta, strict=True), 1):
        mark = "" if a == b else "   <- moved"
        print(f"   {position:>2} {a:>20} {b:>20}{mark}")
    print(f"\n   arms whose position changed: {moved} of {len(ARMS)}")

    # Negative control. Shuffling each item's answers among the respondents keeps every item's
    # RATE and destroys any relationship between items — which is exactly what discrimination
    # measures. If the fitted discriminations survive that, they are the optimiser talking.
    rng = random.Random(SEED)
    scrambled = [row[:] for row in grid]
    for j in range(len(TASKS)):
        column = [scrambled[i][j] for i in range(len(scrambled))]
        rng.shuffle(column)
        for i in range(len(scrambled)):
            scrambled[i][j] = column[i]
    _a, sdisc, _d, sinfo = fit(scrambled)
    live_real = [abs(i.discrimination) for i in live]
    live_fake = [abs(sdisc[j]) for j in range(len(TASKS)) if sinfo[j]]
    print("\n-- negative control: the same fit on answers shuffled within each item --")
    print(f"   median |discrimination|  real {statistics.median(live_real):.2f}"
          f"   shuffled {statistics.median(live_fake):.2f}")
    print(f"   max    |discrimination|  real {max(live_real):.2f}"
          f"   shuffled {max(live_fake):.2f}")

    (HERE / "results.json").write_text(
        json.dumps(
            {
                "respondents": len(names),
                "items": len(TASKS),
                "uninformative_items": [i.task for i in dead],
                "fitted": [
                    {"task": i.task, "rate": round(i.rate, 3),
                     "discrimination": round(i.discrimination, 3),
                     "difficulty": round(i.difficulty, 3)}
                    for i in sorted(live, key=lambda i: -i.discrimination)
                ],
                "rank_by_rate": rank_rate,
                "rank_by_ability": rank_theta,
                "arms_moved": moved,
                "control_median_abs_discrimination": {
                    "real": round(statistics.median(live_real), 3),
                    "shuffled_within_item": round(statistics.median(live_fake), 3),
                },
                "replicas": list(REPLICAS),
            },
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8", newline="\n",
    )
    print("\nwrote results.json")


if __name__ == "__main__":
    main()
