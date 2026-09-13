"""A 2PL item-response model over the factorial we already paid for. Stdlib, deterministic, US$0.

Study 18 shortlist item #12. The respondents are the 24 (arm, replica) runs; the items are the 23
tasks; a cell is pass/fail at the same 0.8 oracle threshold every other bench here uses.

**What it is for.** A benchmark's aggregate score treats every task as equally informative. IRT does
not: it fits a *discrimination* per item, which is how sharply that task separates a good run from a
bad one, and a *difficulty*, which is where on the ability scale it separates them. An item with
discrimination near zero contributes nothing to any comparison — it passes or fails regardless of
who is answering — and averaging it in dilutes every margin the bench reports (arXiv 2609.09372:
choosing by aggregate rank displaces ~22% of appropriate picks).

**Joint maximum likelihood, and its bias is stated rather than hidden.** JML is the estimator a
stdlib file can carry honestly; it is known to be biased outward at small n, and 24 respondents by
23 items is small. So the numbers here are read as an ORDERING of items, never as calibrated
parameters. The scale is fixed by standardising ability each sweep, because a 2PL is only identified
up to a linear transform of it.
"""

from __future__ import annotations

import glob
import json
import math
import os
import statistics
from dataclasses import dataclass
from pathlib import Path

PASS_THRESHOLD = 0.8
TASKS = [
    "011-code-debug", "016-code-repair-pytest", "022-local-rest-api-summary",
    "039-repo-architecture-map", "040-test-coverage-fill", "041-frontend-state-bug",
    "042-api-schema-migration", "043-db-migration-safety", "044-ci-config-repair",
    "045-dependency-upgrade-compat", "047-code-review-risk-report", "051-sql-query-report",
    "064-service-dependency-triage", "080-schema-roundtrip-conversion",
    "082-compose-config-repair", "083-monorepo-interface-repair", "084-js-state-type-bug",
    "085-flaky-test-root-cause", "086-sql-migration-preflight-rollback",
    "087-cli-parser-bug-tests", "089-ab-test-caveat-analysis", "092-schema-drift-audit",
    "094-metric-definition-migration-diff",
]
ARMS = [f"arm-{a}{b}{c}" for a in (0, 1) for b in (0, 1) for c in (0, 1)]
REPLICAS = (0, 1, 2)

SWEEPS = 400
STEP = 0.05
HERE = Path(__file__).resolve().parent


@dataclass
class Item:
    """One task's fitted parameters, plus the raw rate the aggregate would have used."""

    task: str
    rate: float
    discrimination: float
    difficulty: float
    informative: bool
    """False when every respondent answered it the same way — no variance, nothing to fit."""


def _outcome(home: Path, hid: str, task: str) -> float | None:
    hits = glob.glob(str(home / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    if not hits:
        return None
    with open(hits[0], encoding="utf-8") as handle:
        payload = json.load(handle)
    value = (payload.get("oracle_result") or {}).get("outcome_score")
    return float(value) if isinstance(value, (int, float)) else None


def matrix(home: Path | None = None) -> tuple[list[str], list[list[bool | None]]]:
    """(respondent names, respondents × items grid of pass/fail/None)."""
    home = home or Path(os.path.expanduser("~/harness-bench"))
    names: list[str] = []
    grid: list[list[bool | None]] = []
    for arm in ARMS:
        for replica in REPLICAS:
            hid = f"{arm}-r{replica}"
            row: list[bool | None] = []
            for task in TASKS:
                value = _outcome(home, hid, task)
                row.append(None if value is None else value >= PASS_THRESHOLD)
            names.append(hid)
            grid.append(row)
    return names, grid


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-min(x, 60.0)))
    exp = math.exp(max(x, -60.0))
    return exp / (1.0 + exp)


def fit(grid: list[list[bool | None]]) -> tuple[list[float], list[float], list[float], list[bool]]:
    """Joint MLE of (ability per respondent, discrimination and difficulty per item).

    Items with no variance are excluded from the fit and reported as uninformative rather than
    given a fitted parameter: the likelihood is maximised by sending their difficulty to infinity,
    and a number produced that way describes the optimiser, not the task.
    """
    n_resp, n_items = len(grid), len(grid[0])
    informative: list[bool] = []
    for j in range(n_items):
        seen = {grid[i][j] for i in range(n_resp) if grid[i][j] is not None}
        informative.append(len(seen) > 1)

    ability = [0.0] * n_resp
    disc = [1.0] * n_items
    diff = [0.0] * n_items

    for _sweep in range(SWEEPS):
        # Abilities, given the items.
        for i in range(n_resp):
            gradient = 0.0
            for j in range(n_items):
                if not informative[j] or grid[i][j] is None:
                    continue
                p = _sigmoid(disc[j] * (ability[i] - diff[j]))
                gradient += disc[j] * ((1.0 if grid[i][j] else 0.0) - p)
            ability[i] += STEP * gradient
        # A 2PL is identified only up to a linear transform of ability, so pin the scale here.
        mean = statistics.fmean(ability)
        spread = statistics.pstdev(ability) or 1.0
        ability = [(a - mean) / spread for a in ability]

        # Items, given the abilities.
        for j in range(n_items):
            if not informative[j]:
                continue
            g_disc = g_diff = 0.0
            for i in range(n_resp):
                if grid[i][j] is None:
                    continue
                p = _sigmoid(disc[j] * (ability[i] - diff[j]))
                residual = (1.0 if grid[i][j] else 0.0) - p
                g_disc += residual * (ability[i] - diff[j])
                g_diff += -residual * disc[j]
            disc[j] += STEP * g_disc
            diff[j] += STEP * g_diff

    return ability, disc, diff, informative


def items(grid: list[list[bool | None]]) -> list[Item]:
    _ability, disc, diff, informative = fit(grid)
    out: list[Item] = []
    for j, task in enumerate(TASKS):
        answered = [grid[i][j] for i in range(len(grid)) if grid[i][j] is not None]
        rate = sum(1 for a in answered if a) / len(answered) if answered else float("nan")
        out.append(Item(task, rate, disc[j], diff[j], informative[j]))
    return out
