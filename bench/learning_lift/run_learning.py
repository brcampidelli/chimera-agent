"""Does accumulated learning actually help? — the symmetric half of the continuous-evolution bench.

`chimera/eval/continuous.py` measures whether performance *holds* across chained tasks. Nothing
measured whether it *improves*, and the flagship weak-model-lift bench disables learning in both arms
(`--no-remember --no-collect --no-evolve-skills`), so it deliberately says nothing about accumulation.

This runs the same 30 same-family tasks twice, in the same committed order:

  cold      — learning off, and a FRESH agent home per task: nothing survives from task to task
  learning  — learning on, and ONE agent home for the whole sequence: skills and memory accumulate

and reports a difference-in-differences, because arm `cold`'s own first-half/second-half change is the
drift caused by task ordering and noise. Subtracting it is what isolates the part attributable to
accumulation.

Design, order, metric and predictions are fixed in PREREGISTRATION.md, committed before any model
call. Read the power caveat there before reading a null as "learning does not help".
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIFT = HERE.parent / "local_lift"
sys.path.insert(0, str(LIFT))
sys.path.insert(0, str(HERE.parent.parent))
from tasks import TASKS  # noqa: E402

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/mistralai/mistral-small-3.2-24b-instruct")
_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "240"))
_OUT = Path(os.environ.get("BENCH_OUT", str(HERE / "results")))

# The committed suite: the one family with enough shared structure for transfer to be possible.
# Order is `tasks.py` order and is NOT re-shuffled — see PREREGISTRATION.md.
SUITE = [t for t in TASKS if str(t["id"]).startswith("fix_")]
HALF = len(SUITE) // 2

_SCAFFOLD = ["--repo-map", "--progress-ledger", "--checklist", "--replan", "--max-attempts", "3"]
_ARMS = {
    # Identical scaffolding; the ONLY difference is whether anything survives between tasks.
    "cold": [*_SCAFFOLD, "--no-remember", "--no-collect", "--no-evolve-skills"],
    "learning": [*_SCAFFOLD],
}


def _fresh_workspace(task: dict, root: Path) -> Path:
    """The task's starter files + its test, restored clean. Identical for both arms."""
    ws = root / str(task["id"])
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    for rel, content in task.get("files", {}).items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (ws / task["test"]).write_text(task["test_src"], encoding="utf-8")
    return ws


def _solve(task: dict, ws: Path, arm: str, home: Path) -> None:
    """One attempt. ``home`` is what carries (or does not carry) learning between tasks."""
    env = {**os.environ, "CHIMERA_HOME": str(home)}
    verify = f'"{sys.executable}" -m pytest -q {task["test"]}'
    argv = ["chimera", "solve", str(task["prompt"]), "--workspace", str(ws), "--model", _MODEL,
            "--verify", verify, *_ARMS[arm]]
    with contextlib.suppress(subprocess.TimeoutExpired):
        subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False, env=env)


def _grade(task: dict, ws: Path, tampered: set[str]) -> bool:
    """Restore the pristine test, then run it. Solve may read its gate; it may not be its own judge."""
    test_path = ws / task["test"]
    pristine = str(task["test_src"])
    digest = hashlib.sha256(pristine.encode("utf-8")).hexdigest()
    try:
        on_disk = hashlib.sha256(test_path.read_bytes()).hexdigest()
    except OSError:
        on_disk = "missing"
    if on_disk != digest:
        tampered.add(str(task["id"]))
        with contextlib.suppress(OSError):
            shutil.copytree(ws, _OUT / f"tampered_{task['id']}", dirs_exist_ok=True)
    test_path.write_text(pristine, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", task["test"]],
        cwd=str(ws), capture_output=True, text=True, errors="replace", check=False,
    )
    return proc.returncode == 0


def _skills_learned(home: Path) -> int:
    """How many skills the learning arm actually kept — the pre-registered validity check.

    Zero means the experiment measured nothing, and must be reported that way rather than as evidence
    against learning. Two acceptance gates were found broken in the audit that motivated this bench.
    """
    store = home / "skills.json"
    if not store.exists():
        return 0
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(data, dict):
        return len(data.get("skills", data))
    return len(data) if isinstance(data, list) else 0


def _run_arm(arm: str, root: Path, homes: Path, tampered: set[str]) -> list[bool]:
    """Run the whole committed sequence, in order, for one arm."""
    arm_home = homes / arm
    arm_home.mkdir(parents=True, exist_ok=True)
    results: list[bool] = []
    for index, task in enumerate(SUITE, start=1):
        # `cold` gets a brand-new home per task — the point of the arm is that nothing survives.
        home = arm_home / f"t{index}" if arm == "cold" else arm_home
        home.mkdir(parents=True, exist_ok=True)
        ws = _fresh_workspace(task, root)
        _solve(task, ws, arm, home)
        ok = _grade(task, ws, tampered)
        results.append(ok)
        half = "1st" if index <= HALF else "2nd"
        print(f"  [{arm:8}] {index:2}/{len(SUITE)} {half} {str(task['id']):<22} {'PASS' if ok else 'fail'}", flush=True)
    return results


def _rate(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def main() -> None:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    _OUT.mkdir(parents=True, exist_ok=True)
    print(f"learning-lift · model={_MODEL} · tasks={len(SUITE)} (halves {HALF}/{len(SUITE) - HALF})"
          f" · timeout={_TIMEOUT}s", flush=True)

    root = Path(tempfile.mkdtemp(prefix="chimlearn-ws-"))
    homes = Path(tempfile.mkdtemp(prefix="chimlearn-home-"))
    tampered: set[str] = set()
    try:
        arms = {arm: _run_arm(arm, root, homes, tampered) for arm in ("cold", "learning")}
        learned = _skills_learned(homes / "learning")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(homes, ignore_errors=True)

    halves = {a: (_rate(r[:HALF]), _rate(r[HALF:])) for a, r in arms.items()}
    did = (halves["learning"][1] - halves["learning"][0]) - (halves["cold"][1] - halves["cold"][0])

    print("\n" + "=" * 62, flush=True)
    for arm, (first, second) in halves.items():
        print(f"  {arm:8}  1st half {first:6.1%}   2nd half {second:6.1%}   Δ {second - first:+6.1%}"
              f"   overall {_rate(arms[arm]):6.1%}", flush=True)
    print(f"\n  difference-in-differences: {did:+.1%}", flush=True)
    print(f"  skills kept by the learning arm: {learned}", flush=True)
    if learned == 0:
        print("\n  !! NO LEARNING OCCURRED — the learning arm kept zero skills, so this run measured\n"
              "     nothing about accumulation. Per PREREGISTRATION.md this is NOT evidence that\n"
              "     learning does not help; it means the acceptance path produced no artifact to test.",
              flush=True)
    print(f"  grading integrity: {'TAMPERED: ' + ', '.join(sorted(tampered)) if tampered else 'no arm modified its own test'}",
          flush=True)
    print("\n  n=30 in halves of 15 is small: a null here is UNDERPOWERED, not 'no effect'"
          " (PREREGISTRATION.md).", flush=True)

    (_OUT / "learning.json").write_text(
        json.dumps({
            "model": _MODEL, "tasks": [str(t["id"]) for t in SUITE], "half": HALF,
            "by_arm": {a: {"passed": r, "first_half": halves[a][0], "second_half": halves[a][1]}
                       for a, r in arms.items()},
            "did": did, "skills_learned": learned,
            "graded_against_pristine_test": True, "tests_modified_by_solve": sorted(tampered),
        }, indent=2),
        encoding="utf-8",
    )
    print(f"\n  wrote {(_OUT / 'learning.json')}", flush=True)


if __name__ == "__main__":
    main()
