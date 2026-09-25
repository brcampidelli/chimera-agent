"""Read the M7 step-2 solves: the registered analysis, written before any solve. See PREREGISTRATION.md.

    python -m bench.browser_viewport_tasks.read

The unit is the TASK. Each task's success in an arm is the mean over its replicas; a missing cell
(frozen after two errors) drops out of that mean, and a task missing an arm entirely drops out of the
paired read (both reported). The CI is a percentile bootstrap over tasks, 10,000 resamples, fixed seed.
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.browser_viewport_tasks.tasks import TASKS  # noqa: E402

SOLVES = Path(__file__).resolve().parent / os.environ.get("M7_RESULTS", "results") / "solves"
MARGIN = 0.10  # success may not fall by more than this at the CI's lower bound
POINT_FLOOR = -0.05  # nor by more than this at the point estimate
STRATUM_FLOOR = -0.20  # nor, at the point estimate, in any registered stratum
RESAMPLES, SEED = 10_000, 20260924


def records() -> list[dict[str, Any]]:
    out = []
    for path in sorted(SOLVES.glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if not rec.get("error") and rec.get("usd") is not None:
            out.append(rec)
    return out


def per_task(recs: list[dict[str, Any]], field: Callable[[dict[str, Any]], float]) -> dict[str, dict[str, float]]:
    cells: dict[str, dict[str, list[float]]] = {}
    for r in recs:
        cells.setdefault(r["task"], {}).setdefault(r["arm"], []).append(field(r))
    return {t: {arm: statistics.mean(v) for arm, v in arms.items()} for t, arms in cells.items()}


def bootstrap(values: list[Any], stat: Callable[[list[Any]], float]) -> tuple[float, float, float]:
    """Point estimate and percentile 95% CI, resampling TASKS (the items of `values`)."""
    rng = random.Random(SEED)
    draws = sorted(stat([rng.choice(values) for _ in values]) for _ in range(RESAMPLES))
    return stat(values), draws[int(0.025 * RESAMPLES)], draws[int(0.975 * RESAMPLES) - 1]


def ratio_of_means(pairs: list[tuple[float, float]]) -> float:
    """Viewport tokens over today's, summed over tasks: what the spend follows (heavy pages weigh
    more). The mean of per-task ratios is printed beside it and decides nothing."""
    today = sum(t for t, _ in pairs)
    return sum(v for _, v in pairs) / today if today else float("nan")


def paired(table: dict[str, dict[str, float]], ids: list[str]) -> list[tuple[float, float]]:
    return [(table[t]["today"], table[t]["viewport"]) for t in ids if t in table and len(table[t]) == 2]


def main() -> None:
    recs = records()
    if not recs:
        print("no finished solves yet")
        return
    success = per_task(recs, lambda r: float(bool(r.get("success"))))
    tokens = per_task(recs, lambda r: float(r.get("prompt_tokens") or 0))
    steps = per_task(recs, lambda r: float(r.get("steps") or 0))
    usd = per_task(recs, lambda r: float(r.get("usd") or 0))
    uncached = per_task(recs, lambda r: float((r.get("prompt_tokens") or 0) - (r.get("cache_read_tokens") or 0)))
    ids = [t.id for t in TASKS]
    pairs = paired(success, ids)
    print(f"solves read: {len(recs)}; tasks with both arms: {len(pairs)} of {len(ids)}")

    diff = bootstrap([v - t for t, v in pairs], statistics.mean)
    tok_pairs = paired(tokens, ids)
    ratio = bootstrap(tok_pairs, ratio_of_means)
    per_task_ratio = statistics.mean(v / t for t, v in tok_pairs if t > 0)
    print(f"PRIMARY  success viewport - today: {diff[0]:+.3f}  95% CI [{diff[1]:+.3f}, {diff[2]:+.3f}]")
    print(f"         success today {statistics.mean(t for t, _ in pairs):.3f}, "
          f"viewport {statistics.mean(v for _, v in pairs):.3f}")
    print(f"SECOND.  prompt tokens per solve, viewport/today (ratio of means over tasks): {ratio[0]:.3f}  "
          f"95% CI [{ratio[1]:.3f}, {ratio[2]:.3f}]; mean of per-task ratios {per_task_ratio:.3f}")
    # A saving that lives only in cached prefix tokens is a saving at the cache's price, not the full
    # one (the plan's "savings in input tokens without the cache price"). The receipt cannot tell —
    # it prices every prompt token at the full rate — so the uncached tokens are read separately.
    money = bootstrap(paired(uncached, ids), ratio_of_means)
    print(f"         UNCACHED prompt tokens per solve (prompt - cache reads), viewport/today: "
          f"{money[0]:.3f}  95% CI [{money[1]:.3f}, {money[2]:.3f}]")
    receipt = bootstrap(paired(usd, ids), ratio_of_means)
    print(f"         receipt US$ per solve, viewport/today (catalogue price, cache not discounted): "
          f"{receipt[0]:.3f}  95% CI [{receipt[1]:.3f}, {receipt[2]:.3f}]")
    p = paired(steps, ids)
    d = bootstrap([v - t for t, v in p], statistics.mean)
    print(f"         steps per solve viewport - today: {d[0]:+.3f}  95% CI [{d[1]:+.3f}, {d[2]:+.3f}]")

    strata = {
        "in_view": [t.id for t in TASKS if t.stratum == "in_view"],
        "below": [t.id for t in TASKS if t.stratum == "below"],
        "hurt_prone": [t.id for t in TASKS if t.hurt_prone],
    }
    worst = 0.0
    for name, members in strata.items():
        p = paired(success, members)
        if not p:
            continue
        d = statistics.mean(v - t for t, v in p)
        worst = min(worst, d)
        print(f"STRATUM  {name:<10} n={len(p):>2} success today {statistics.mean(t for t, _ in p):.3f} "
              f"viewport {statistics.mean(v for _, v in p):.3f}  diff {d:+.3f}")

    adopt = (diff[1] >= -MARGIN and diff[0] >= POINT_FLOOR and ratio[2] < 1.0 and money[0] < 1.0
             and worst >= STRATUM_FLOOR)
    loss = diff[2] < 0
    verdict = ("ADOPT as default" if adopt else
               "stays OPT-IN; the success CI lies wholly below zero — a loss, recorded" if loss else
               "stays OPT-IN")
    print(f"DECISION {verdict}  (rule: success CI low >= -{MARGIN} and point >= {POINT_FLOOR}; token-ratio "
          f"CI high < 1; uncached-token ratio < 1; every stratum >= {STRATUM_FLOOR})")


if __name__ == "__main__":
    main()
