"""retry-lift: does conditioning a retry on its own failed attempt help?

Executes the design fixed in ``PREREGISTRATION.md`` (+ Amendment 1). Read that file first — it fixes
the arms, the metrics, the predictions, the validity gates and the pre-committed readings, and it was
committed before this script existed.

Three arms over the SAME 40 hard tasks in committed order, 3 seeds, no cross-task learning anywhere
(fresh agent home per task in every arm), so what varies is only how a *retry* is conditioned:

  control  current behaviour
  i1       + --diff-feedback     show the failed attempt its own reverted diff
  i2       + --stagnation-fuzzy  match repeated-failure signatures approximately

Two things this runner does that the learning-lift bench did not, both paid for by past failures:

* **It keeps stdout.** Attempt counts and injection events are printed by ``chimera solve``; the
  learning-lift bench discarded them with ``capture_output=True``, which is why runs 2-7 cannot say
  how often a retry even happened. Here they are parsed and reported.
* **It can declare itself invalid.** Asymmetric timeouts between arms, a dead injection wire, or a
  control that drifts off the probe's numbers each make the run report a measurement failure rather
  than a result. A bench that cannot fail honestly is not a bench.

Usage (WSL):  BENCH_TIMEOUT=480 uv run --extra dev python bench/retry_lift/run_retry.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "learning_lift"))

from run_learning import _MODEL, _fresh_workspace, _grade  # noqa: E402
from tasks_hard_fix import HARD_FIX_TASKS as TASKS  # noqa: E402

from chimera.eval.paired import compare_paired  # noqa: E402

_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "480"))
_SEEDS = int(os.environ.get("BENCH_SEEDS", "3"))
_OUT = Path(os.environ.get("BENCH_OUT", str(Path(__file__).resolve().parent / "results")))

# Identical scaffolding everywhere; the ONLY difference between arms is the retry conditioning.
# --stream makes the injection events printable so the wire can be counted (Amendment 1); it only
# prints, is passed to all three arms, and therefore cannot bias the comparison.
_BASE = ["--repo-map", "--progress-ledger", "--checklist", "--replan", "--max-attempts", "3",
         "--no-remember", "--no-collect", "--no-evolve-skills", "--stream"]
_ARMS: dict[str, list[str]] = {
    "control": [*_BASE],
    "i1": [*_BASE, "--diff-feedback"],
    "i2": [*_BASE, "--stagnation-fuzzy"],
}

# The probe's control numbers (bench/learning_lift/results_probe/probe.json), used as the drift gate.
_PROBE_A_CI = (0.375, 0.671)   # share of solves whose attempt 1 failed
_PROBE_R_CI = (0.208, 0.591)   # share of retried solves that recovered

_ATTEMPTS_RE = re.compile(r"after (\d+) attempt")
_DIFF_INJECT_RE = re.compile(r"diff-feedback injected")
# I2 fires whenever the detector calls a stall — but WHICH event that produces depends on the
# scaffolding. With --replan on (all three arms here) the dual-ledger branch wins and emits
# "re-planned after stall"; the advisory "stagnation pivot injected" branch is its `else` and is
# therefore UNREACHABLE in this configuration. Counting only the pivot would have reported zero
# firings for a live intervention and failed I2's own validity gate for the wrong reason.
# Both are counted, and because control also runs --replan, the control-vs-i2 difference in this
# count is the direct measure of what approximate matching actually changed.
_PIVOT_INJECT_RE = re.compile(r"stagnation pivot injected|re-planned after stall")


def _solve(task: dict, ws: Path, arm: str, home: Path) -> dict[str, object]:
    """One solve. Returns attempts / injections / duration / timeout — never a silent failure."""
    verify = f"{sys.executable} -m pytest -q {task['test']}"
    argv = ["chimera", "solve", str(task["prompt"]), "--workspace", str(ws), "--model", _MODEL,
            "--verify", verify, *_ARMS[arm]]
    env = {**os.environ, "CHIMERA_HOME": str(home)}
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=_TIMEOUT, check=False, env=env)
        out = proc.stdout or ""
    except subprocess.TimeoutExpired:
        return {"attempts": None, "diff_injections": 0, "pivot_injections": 0,
                "timed_out": True, "seconds": round(time.monotonic() - started, 1)}
    match = _ATTEMPTS_RE.search(out)
    return {
        "attempts": int(match.group(1)) if match else None,
        "diff_injections": len(_DIFF_INJECT_RE.findall(out)),
        "pivot_injections": len(_PIVOT_INJECT_RE.findall(out)),
        "timed_out": False,
        "seconds": round(time.monotonic() - started, 1),
    }


def _run_arm(arm: str, seed: int, root: Path, homes: Path, tampered: set[str]) -> list[dict]:
    arm_home = homes / f"{arm}_s{seed}"
    rows: list[dict] = []
    for index, task in enumerate(TASKS, start=1):
        ws = _fresh_workspace(task, root / f"{arm}_s{seed}")
        home = arm_home / f"t{index}"   # fresh per task in EVERY arm: no cross-task learning anywhere
        home.mkdir(parents=True, exist_ok=True)
        row = _solve(task, ws, arm, home)
        row |= {"seed": seed, "arm": arm, "id": task["id"], "passed": _grade(task, ws, tampered)}
        rows.append(row)
        print(f"    [{index:>2}/{len(TASKS)}] {arm:<7} {task['id']:<32} "
              f"{'PASS' if row['passed'] else 'FAIL'}  att={row['attempts']} "
              f"inj={row['diff_injections']}/{row['pivot_injections']}  {row['seconds']:.0f}s", flush=True)
    return rows


def main() -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="retrylift-ws-"))
    homes = Path(tempfile.mkdtemp(prefix="retrylift-home-"))
    rows: list[dict] = []
    tampered: set[str] = set()
    print(f"retry-lift: {len(TASKS)} tasks x {len(_ARMS)} arms x {_SEEDS} seed(s) "
          f"= {len(TASKS) * len(_ARMS) * _SEEDS} solves | model={_MODEL} | timeout={_TIMEOUT}s",
          flush=True)
    try:
        for seed in range(1, _SEEDS + 1):
            for arm in _ARMS:
                print(f"\n  -- seed {seed}, arm {arm} --", flush=True)
                rows.extend(_run_arm(arm, seed, root, homes, tampered))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(homes, ignore_errors=True)

    report = _report(rows, tampered)
    (_OUT / "retry.json").write_text(
        json.dumps({"model": _MODEL, "suite": "hard_fix", "seeds": _SEEDS,
                    "rows": rows, "tampered": sorted(tampered), "report": report}, indent=2),
        encoding="utf-8")
    print(f"\n  wrote {_OUT / 'retry.json'}", flush=True)


def _key(row: dict) -> tuple[int, str]:
    return (int(row["seed"]), str(row["id"]))


def _paired(rows: list[dict], treatment: str) -> dict[str, object]:
    """Pooled paired control-vs-treatment, plus the pre-registered retried-solve lens."""
    ctrl = {_key(r): r for r in rows if r["arm"] == "control"}
    treat = {_key(r): r for r in rows if r["arm"] == treatment}
    shared = sorted(ctrl.keys() & treat.keys())
    pooled = compare_paired([bool(ctrl[k]["passed"]) for k in shared],
                            [bool(treat[k]["passed"]) for k in shared],
                            baseline_name="control", treatment_name=treatment)
    # Retried lens: BOTH arms had to retry, so the pairing is genuine (PREREGISTRATION §5).
    both = [k for k in shared
            if isinstance(ctrl[k]["attempts"], int) and int(ctrl[k]["attempts"]) >= 2
            and isinstance(treat[k]["attempts"], int) and int(treat[k]["attempts"]) >= 2]
    only_one = sum(1 for k in shared
                   if (isinstance(ctrl[k]["attempts"], int) and int(ctrl[k]["attempts"]) >= 2)
                   != (isinstance(treat[k]["attempts"], int) and int(treat[k]["attempts"]) >= 2))
    lens = compare_paired([bool(ctrl[k]["passed"]) for k in both],
                          [bool(treat[k]["passed"]) for k in both],
                          baseline_name="control", treatment_name=treatment) if both else None
    return {"pooled": pooled.summary(), "retried_lens": lens.summary() if lens else None,
            "retried_both": len(both), "retried_one_arm_only": only_one}


def _arm_stats(rows: list[dict], arm: str) -> dict[str, object]:
    arm_rows = [r for r in rows if r["arm"] == arm]
    known = [r for r in arm_rows if isinstance(r["attempts"], int)]
    retried = [r for r in known if int(r["attempts"]) >= 2]
    recovered = [r for r in retried if r["passed"]]
    return {
        "n": len(arm_rows),
        "unparsed": len(arm_rows) - len(known),
        "timeouts": sum(1 for r in arm_rows if r["timed_out"]),
        "pass_rate": round(sum(1 for r in arm_rows if r["passed"]) / len(arm_rows), 4) if arm_rows else 0.0,
        "A_attempt1_failed": round(len(retried) / len(known), 4) if known else 0.0,
        "R_recovered": round(len(recovered) / len(retried), 4) if retried else 0.0,
        "attempts_hist": dict(sorted(Counter(int(r["attempts"]) for r in known).items())),
        "median_seconds": round(statistics.median([float(r["seconds"]) for r in arm_rows]), 1) if arm_rows else 0.0,
        "diff_injections": sum(int(r["diff_injections"]) for r in arm_rows),
        "pivot_injections": sum(int(r["pivot_injections"]) for r in arm_rows),
    }


def _report(rows: list[dict], tampered: set[str]) -> dict[str, object]:
    stats = {arm: _arm_stats(rows, arm) for arm in _ARMS}
    comparisons = {arm: _paired(rows, arm) for arm in ("i1", "i2")}
    gates = _gates(stats, tampered)

    print("\n" + "=" * 78)
    print(f"  RETRY-LIFT — {len(TASKS)} tasks x {_SEEDS} seed(s), hard suite, model {_MODEL}")
    print("\n  per arm:")
    for arm, s in stats.items():
        print(f"    {arm:<8} pass {s['pass_rate']:6.1%} | A {s['A_attempt1_failed']:6.1%} "
              f"| R {s['R_recovered']:6.1%} | attempts {s['attempts_hist']} "
              f"| median {s['median_seconds']:.0f}s | timeouts {s['timeouts']} | unparsed {s['unparsed']}")
        print(f"             injections: diff={s['diff_injections']}  pivot={s['pivot_injections']}")

    for arm, comp in comparisons.items():
        pooled = comp["pooled"]
        assert isinstance(pooled, dict)
        lo, hi = pooled["diff_ci"]  # type: ignore[index]
        sig = "SIGNIFICANT" if pooled["significant"] else "not significant"
        print(f"\n  {arm.upper()} vs control — PRIMARY (pooled paired, n={pooled['n']}):")
        print(f"    control {pooled['baseline_rate']:.1%}  {arm} {pooled['treatment_rate']:.1%}  "
              f"Δ {pooled['delta']:+.1%}  95% CI [{lo:+.1%}, {hi:+.1%}]  -> {sig}")
        print(f"    discordant: {arm} +{pooled['discordant']['treatment_only']} / "  # type: ignore[index]
              f"control +{pooled['discordant']['baseline_only']}")  # type: ignore[index]
        lens = comp["retried_lens"]
        if isinstance(lens, dict):
            llo, lhi = lens["diff_ci"]  # type: ignore[index]
            lsig = "SIGNIFICANT" if lens["significant"] else "not significant"
            print(f"    SECONDARY (retried lens, both arms retried, n={lens['n']}; "
                  f"{comp['retried_one_arm_only']} pair(s) retried by one arm only, excluded):")
            print(f"      control {lens['baseline_rate']:.1%}  {arm} {lens['treatment_rate']:.1%}  "
                  f"Δ {lens['delta']:+.1%}  95% CI [{llo:+.1%}, {lhi:+.1%}]  -> {lsig}")
        else:
            print("    SECONDARY: no pair had BOTH arms retry — the lens has no data.")

    print("\n  VALIDITY GATES (PREREGISTRATION §7):")
    for name, (ok, detail) in gates.items():
        print(f"    [{'ok ' if ok else 'FAIL'}] {name}: {detail}")
    if not all(ok for ok, _ in gates.values()):
        print("\n  !! A gate failed. Per the pre-registration this run reports a MEASUREMENT")
        print("     FAILURE, not evidence for or against either intervention.")
    print("=" * 78)
    return {"arms": stats, "comparisons": comparisons,
            "gates": {k: {"ok": v[0], "detail": v[1]} for k, v in gates.items()}}


def _gates(stats: dict[str, dict], tampered: set[str]) -> dict[str, tuple[bool, str]]:
    ctrl = stats["control"]
    a, r = float(ctrl["A_attempt1_failed"]), float(ctrl["R_recovered"])
    timeouts = {arm: int(s["timeouts"]) for arm, s in stats.items()}
    worst, best = max(timeouts.values()), min(timeouts.values())
    return {
        "wire live (I1)": (
            int(stats["i1"]["diff_injections"]) > 0,
            f"{stats['i1']['diff_injections']} diff injections in the i1 arm "
            f"(0 = plumbing failure, NOT evidence against the idea)",
        ),
        # Not just ">0": control runs --replan too, so both arms stall-detect. If approximate
        # matching finds the SAME stalls as exact matching, the i2 arm is the control arm and any
        # measured delta is noise — an inert intervention, which must be reported as "nothing to
        # measure" rather than as "I2 does not help".
        "wire live (I2)": (
            int(stats["i2"]["pivot_injections"]) > int(stats["control"]["pivot_injections"]),
            f"{stats['i2']['pivot_injections']} stall responses in i2 vs "
            f"{stats['control']['pivot_injections']} in control "
            f"(equal = approximate matching changed nothing; the arm is inert, not refuted)",
        ),
        "timeout symmetry": (
            worst - best <= 1,
            f"per-arm timeouts {timeouts} (asymmetry invalidates the run)",
        ),
        "control drift (A)": (
            _PROBE_A_CI[0] <= a <= _PROBE_A_CI[1],
            f"control A={a:.1%}, probe CI [{_PROBE_A_CI[0]:.1%}, {_PROBE_A_CI[1]:.1%}]",
        ),
        "control drift (R)": (
            _PROBE_R_CI[0] <= r <= _PROBE_R_CI[1],
            f"control R={r:.1%}, probe CI [{_PROBE_R_CI[0]:.1%}, {_PROBE_R_CI[1]:.1%}]",
        ),
        "retried lens informative": (
            0.20 <= r <= 0.80,
            f"control R={r:.1%} (outside 20-80% = ceiling/floor, lens reported-not-interpreted)",
        ),
        "grading integrity": (
            not tampered,
            f"{len(tampered)} task(s) modified their own test" if tampered else "no arm modified its own test",
        ),
    }


if __name__ == "__main__":
    main()
