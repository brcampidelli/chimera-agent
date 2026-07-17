"""Paired weak-model-lift experiment (M15-C1) — baseline vs the full loop from the IDENTICAL state.

`run_ci.py` runs one arm at a time and the two are compared *unpaired* (Newcombe), so the variance
from starting conditions widens the CI — which is exactly why the earlier n=6 run could not reach
significance. This runner uses the M15-B1 machinery instead: for each task it restores the SAME
fresh workspace before each arm (the fork/identical-state discipline), runs the baseline arm and the
full Chimera arm, grades each independently with the task's own pytest, and reports the *paired*
(McNemar/Wilson) verdict alongside the unpaired one. Pairing conditions out the tasks both arms agree
on, so a real lift can clear zero at the small n a disposable test key affords.

Honest by construction: the verdict is the tests' word (pytest re-run independently), never solve's
self-report; both the paired and unpaired numbers are printed so the reader sees what pairing buys.

Configure via env: BENCH_MODEL (a cheap tool-capable model), BENCH_TIMEOUT (per-solve seconds, per
arm), BENCH_TASKS (comma ids). The orchestration core `run_paired` takes injected solve/grade
callables, so it is unit-testable offline; `main()` wires the real `chimera solve` subprocess.

Long runs are resumable (`results/journal.jsonl`), and the resume rule is a *scientific* constraint,
not a convenience: a **completed pair is never re-run**. The journal is append-only and the first
complete result for a task wins for good. Re-running a pair you have already seen is exactly how you
would roll a task until it passes, which is the p-hacking this bench exists to refuse. A pair
interrupted *between* its two arms is discarded whole and replayed from a fresh restore — the
half-finished arm's result is thrown away too, so no outcome is ever selected after the fact.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tasks import TASKS  # noqa: E402

# Repo root (…/bench/local_lift → repo root), so the pure-Python imports resolve when run directly.
sys.path.insert(0, str(HERE.parent.parent))

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/meta-llama/llama-3.1-8b-instruct")
_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "300"))
_ONLY = {t.strip() for t in os.environ.get("BENCH_TASKS", "").split(",") if t.strip()}
_HYGIENE = ["--no-remember", "--no-collect", "--no-evolve-skills"]
# The only variable between arms is Chimera's scaffolding (same model, same task, same start state).
_ARM_FLAGS = {
    "baseline": ["--no-plan", "--no-manager", "--max-attempts", "1", *_HYGIENE],
    "chimera": ["--repo-map", "--progress-ledger", "--checklist", "--replan", "--max-attempts", "3", *_HYGIENE],
}

# Injected callables (typed for the testable core):
#   SolveFn(task, workspace, arm) -> None   — run one arm in `workspace`
#   GradeFn(task, workspace) -> bool         — the independent pass/fail verdict
SolveFn = Callable[[dict, Path, str], None]
GradeFn = Callable[[dict, Path], bool]


def _fresh_workspace(task: dict, root: Path) -> Path:
    """Restore the task's starter files + test into a clean workspace — the per-arm 'fork'."""
    ws = root / task["id"]
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    for rel, content in task.get("files", {}).items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (ws / task["test"]).write_text(task["test_src"], encoding="utf-8")
    return ws


def _load_journal(journal: Path | None, *, model: str) -> dict[str, dict[str, bool]]:
    """Completed pairs from an earlier, interrupted run of this same suite+model.

    Only *complete* pairs are ever written (see :func:`_append_journal`), so anything here is a
    finished, immutable observation. Records from a different model are ignored rather than reused —
    a cached cell is only a valid resume if it was produced under the same conditions.
    """
    done: dict[str, dict[str, bool]] = {}
    if journal is None or not journal.exists():
        return done
    for line in journal.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        with contextlib.suppress(json.JSONDecodeError):
            rec = json.loads(line)
            if not isinstance(rec, dict) or rec.get("model") != model:
                continue
            if {"task", "baseline", "chimera"} <= rec.keys():
                done[str(rec["task"])] = {"baseline": bool(rec["baseline"]), "chimera": bool(rec["chimera"])}
    return done


def _append_journal(journal: Path | None, *, task_id: str, model: str, base: bool, treat: bool) -> None:
    """Commit one *finished pair*. Written only once both arms are in — never a half pair."""
    if journal is None:
        return
    journal.parent.mkdir(parents=True, exist_ok=True)
    rec = {"task": task_id, "model": model, "baseline": base, "chimera": treat}
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def run_paired(  # type: ignore[no-untyped-def]
    tasks: list[dict], *, solve: SolveFn, grade: GradeFn, root: Path,
    journal: Path | None = None, model: str = "",
):
    """Run both arms from the identical restored state per task; return the paired result + rows.

    Uses :func:`chimera.eval.paired.run_paired_experiment` so the discipline (restore before each
    arm) and the statistic (McNemar/Wilson) are the tested B1 code, not re-implemented here.

    With a `journal`, pairs already completed by an earlier process are replayed from the record
    instead of being re-run — see the module docstring for why that direction (never re-run a
    finished pair) is the honest one.
    """
    from chimera.eval.paired import run_paired_experiment

    rows: list[dict] = []
    done = _load_journal(journal, model=model)
    # A baseline result held until its treatment lands, so the pair is journalled atomically.
    pending: dict[str, bool] = {}

    def restore(task: dict) -> None:
        _fresh_workspace(task, root)

    def _arm(task: dict, arm: str) -> bool:
        ws = root / task["id"]
        solve(task, ws, arm)
        return grade(task, ws)

    def baseline(task: dict) -> bool:
        tid = str(task["id"])
        if tid in done:
            ok = done[tid]["baseline"]  # resumed — NOT re-run
        else:
            ok = _arm(task, "baseline")
            pending[tid] = ok
        rows.append({"task": tid, "arm": "baseline", "passed": ok})
        return ok

    def treatment(task: dict) -> bool:
        tid = str(task["id"])
        if tid in done:
            ok = done[tid]["chimera"]  # resumed — NOT re-run
        else:
            ok = _arm(task, "chimera")
            # Both arms are in: the pair is now a finished observation, so record it for good.
            _append_journal(journal, task_id=tid, model=model, base=pending.pop(tid, False), treat=ok)
        rows.append({"task": tid, "arm": "chimera", "passed": ok})
        return ok

    result = run_paired_experiment(
        tasks, restore=restore, baseline=baseline, treatment=treatment,
        baseline_name="raw-model", treatment_name="chimera",
    )
    return result, rows


def _real_solve(task: dict, ws: Path, arm: str) -> None:
    verify = f'"{sys.executable}" -m pytest -q {task["test"]}'
    argv = ["chimera", "solve", task["prompt"], "--workspace", str(ws), "--model", _MODEL,
            "--verify", verify, *_ARM_FLAGS[arm]]
    # A timed-out arm simply fails its pytest below — an honest failure, not a crash.
    with contextlib.suppress(subprocess.TimeoutExpired):
        subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False)


def _real_grade(task: dict, ws: Path) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", task["test"]],
        cwd=str(ws), capture_output=True, text=True, errors="replace", check=False,
    )
    return proc.returncode == 0


def main() -> None:
    # The paired report prints a Δ (delta) glyph; on a Windows cp1252 console that raised
    # UnicodeEncodeError and aborted BEFORE results/paired.json was written. Make stdout UTF-8-safe.
    with contextlib.suppress(Exception):  # best-effort console fix; never block the run
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    tasks = [t for t in TASKS if not _ONLY or t["id"] in _ONLY]
    out = HERE / "results"
    journal = out / "journal.jsonl"
    resumed = len(_load_journal(journal, model=_MODEL))
    print(f"paired experiment · model={_MODEL} · tasks={len(tasks)} · timeout={_TIMEOUT}s/arm", flush=True)
    if resumed:
        # Say it out loud: part of this verdict was observed by an earlier process, not this one.
        print(f"resuming · {resumed} pair(s) replayed from {journal.name} (finished pairs are never re-run)", flush=True)
    root = Path(tempfile.mkdtemp(prefix="chimpaired-"))
    try:
        result, rows = run_paired(
            tasks, solve=_real_solve, grade=_real_grade, root=root, journal=journal, model=_MODEL
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # Per-task outcome (baseline vs chimera), printed and persisted for the record.
    by_task: dict[str, dict[str, bool]] = {}
    for row in rows:
        by_task.setdefault(str(row["task"]), {})[str(row["arm"])] = bool(row["passed"])
    print("", flush=True)
    for tid, arms in by_task.items():
        b, c = arms.get("baseline"), arms.get("chimera")
        mark = "  RECOVERED" if b is False and c is True else ""
        print(f"  {tid:<16} raw={'PASS' if b else 'fail'}  chimera={'PASS' if c else 'fail'}{mark}", flush=True)

    # Reconstruct the aligned per-arm lists from the paired counts is lossy; instead re-derive from
    # the result object which already holds a/b/c/d. Print the paired verdict (the point of C1)…
    from chimera.eval.paired import format_report as format_paired

    print("\n" + format_paired(result), flush=True)

    # …and the unpaired Newcombe verdict on the same marginals, so the tightening is visible.
    print(
        f"\n[unpaired marginals] raw-model {result.baseline_rate:.0%} vs chimera "
        f"{result.treatment_rate:.0%} — paired is the tighter, honest read here.",
        flush=True,
    )

    out.mkdir(exist_ok=True)
    (out / "paired.json").write_text(
        json.dumps(
            {"model": _MODEL, "summary": result.summary(), "by_task": by_task, "resumed_pairs": resumed},
            indent=2,
        ),
        encoding="utf-8",
    )

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text(
            f"### Paired weak-model lift on `{_MODEL}`\n\n```\n{format_paired(result)}\n```\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
