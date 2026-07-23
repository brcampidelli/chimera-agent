"""Sizing probe: how much work does the verify-or-revert RETRY loop actually do?

Not an experiment — a measurement of a *population*, run before deciding whether to build anything.

The proposal it sizes: after a failed attempt, show the model the diff it actually wrote (which the
agent already captures in ``Attempt.diffs`` before reverting, and then throws away) so the retry is
conditioned against re-deriving the same wrong edit. That mechanism can only act on solves that
reach attempt >= 2, so the first question is not "does it work" but "how many solves does it even
touch, and what happens to them today".

Three numbers decide whether the idea is worth building:

  A. share of solves where attempt 1 FAILED          -> the population the mechanism can act on at all
  R. P(final pass | attempt 1 failed)                -> how well the retry loop already recovers
  X. share of FAILURES that exhausted the budget     -> whether failures are "retried and still lost"
                                                        (room to improve) or "failed fast" (a different
                                                        bug entirely, and this mechanism is irrelevant)

If A is small, the mechanism is a rounding error no matter how good it is. If X is small, the retry
loop is not even engaging on the failures and the wrong thing is being fixed.

Design notes:
  * COLD ARM ONLY. The retry loop is identical in both arms — learning changes what is injected into
    context, not how attempts are sequenced — so measuring one arm halves the cost for the same answer.
  * The HARD DISJOINT suite (``tasks_hard_fix``), where the control ran ~50-60%: failures are what
    carry the signal here, so the suite with the most of them is the informative one. On the 88%
    recurring suites almost every solve passes on attempt 1 and the probe would learn nothing.
  * Attempt counts are parsed from ``chimera solve`` stdout, which always prints
    "<status> after N attempt(s)" (``chimera/cli/main.py``). The learning-lift bench discarded this
    with ``capture_output=True``, which is why runs 2-7 cannot answer the question retroactively.
  * Pass/fail is graded independently against the PRISTINE test, same as the bench: solve may read
    its gate, it may not be its own judge.

Usage (WSL):  BENCH_TIMEOUT=480 uv run --extra dev python bench/learning_lift/probe_attempts.py
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_learning import _ARMS, _MODEL, _fresh_workspace, _grade  # noqa: E402
from tasks_hard_fix import HARD_FIX_TASKS as TASKS  # noqa: E402

_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "480"))
_OUT = Path(os.environ.get("PROBE_OUT", str(Path(__file__).resolve().parent / "results_probe")))
_ATTEMPTS_RE = re.compile(r"after (\d+) attempt")


def _solve_capturing(task: dict, ws: Path, home: Path) -> tuple[int | None, bool, float]:
    """Run one cold-arm solve; return (attempts_used, timed_out, elapsed_seconds).

    ``attempts_used`` is None when the count could not be parsed — reported separately rather than
    folded into the distribution, so a parsing gap can never masquerade as a 1-attempt solve.
    """
    verify = f"{sys.executable} -m pytest -q {task['test']}"
    argv = ["chimera", "solve", str(task["prompt"]), "--workspace", str(ws), "--model", _MODEL,
            "--verify", verify, *_ARMS["cold"]]
    env = {**os.environ, "CHIMERA_HOME": str(home)}
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=_TIMEOUT, check=False, env=env)
    except subprocess.TimeoutExpired:
        return None, True, time.monotonic() - started
    match = _ATTEMPTS_RE.search(proc.stdout or "")
    return (int(match.group(1)) if match else None), False, time.monotonic() - started


def main() -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="chimprobe-ws-"))
    homes = Path(tempfile.mkdtemp(prefix="chimprobe-home-"))
    rows: list[dict[str, object]] = []
    tampered: set[str] = set()

    print(f"probe: {len(TASKS)} hard tasks, cold arm, model={_MODEL}, timeout={_TIMEOUT}s", flush=True)
    try:
        for index, task in enumerate(TASKS, start=1):
            ws = _fresh_workspace(task, root)
            home = homes / f"t{index}"          # fresh home per task: a true no-learning control
            home.mkdir(parents=True, exist_ok=True)
            attempts, timed_out, elapsed = _solve_capturing(task, ws, home)
            passed = _grade(task, ws, tampered)
            rows.append({"id": task["id"], "attempts": attempts, "passed": passed,
                         "timed_out": timed_out, "seconds": round(elapsed, 1)})
            print(f"  [{index:>2}/{len(TASKS)}] {task['id']:<34} "
                  f"{'PASS' if passed else 'FAIL'}  attempts={attempts}  {elapsed:.0f}s", flush=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(homes, ignore_errors=True)

    _report(rows, tampered)
    (_OUT / "probe.json").write_text(
        json.dumps({"model": _MODEL, "suite": "hard_fix", "arm": "cold", "rows": rows,
                    "tampered": sorted(tampered)}, indent=2), encoding="utf-8")
    print(f"\n  wrote {_OUT / 'probe.json'}", flush=True)


def _report(rows: list[dict[str, object]], tampered: set[str]) -> None:
    known = [r for r in rows if isinstance(r["attempts"], int)]
    unparsed = len(rows) - len(known)
    timeouts = sum(1 for r in rows if r["timed_out"])
    if not known:
        print("\n  no attempt counts parsed — the probe measured nothing. Do not interpret.")
        return

    total = len(known)
    passes = [r for r in known if r["passed"]]
    fails = [r for r in known if not r["passed"]]
    retried = [r for r in known if int(r["attempts"]) >= 2]        # attempt 1 failed
    recovered = [r for r in retried if r["passed"]]
    budget = max(int(r["attempts"]) for r in known)
    exhausted = [r for r in fails if int(r["attempts"]) >= budget]

    print("\n" + "=" * 66)
    print(f"  RETRY-LOOP SIZING (n={total} cold solves on the hard suite)")
    if unparsed or timeouts:
        print(f"    excluded: {unparsed} unparsed, {timeouts} timed out  "
              f"(reported, never counted as 1-attempt)")
    if tampered:
        print(f"    !! {len(tampered)} task(s) had their test modified — graded against pristine")
    print(f"    overall pass rate: {len(passes) / total:.1%}")
    print()
    print(f"  A. attempt 1 FAILED:            {len(retried)}/{total} = {len(retried) / total:.1%}"
          "   <- the population this mechanism can act on")
    if retried:
        print(f"  R. recovered after retrying:    {len(recovered)}/{len(retried)}"
              f" = {len(recovered) / len(retried):.1%}   <- what the retry loop already achieves")
    print(f"  X. failures that used all {budget}:     {len(exhausted)}/{len(fails) or 1}"
          f" = {(len(exhausted) / len(fails)) if fails else 0:.1%}"
          "   <- retried-and-still-lost = the headroom")
    print()
    print(f"    attempts among PASSES:   {dict(sorted(Counter(int(r['attempts']) for r in passes).items()))}")
    print(f"    attempts among FAILURES: {dict(sorted(Counter(int(r['attempts']) for r in fails).items()))}")
    print(f"    median seconds: pass {statistics.median([r['seconds'] for r in passes] or [0]):.0f}s"
          f" | fail {statistics.median([r['seconds'] for r in fails] or [0]):.0f}s")
    print()
    # The honest ceiling: a perfect retry converts the retried-and-lost solves and nothing else.
    ceiling = len(exhausted) / total
    print(f"  CEILING on this mechanism: +{ceiling:.1%} absolute pass rate")
    print("    (a PERFECT retry converts every retried-and-still-lost solve and nothing more;")
    print("     any real intervention gets a fraction of this. If it is small, do not build.)")
    print("=" * 66)


if __name__ == "__main__":
    main()
