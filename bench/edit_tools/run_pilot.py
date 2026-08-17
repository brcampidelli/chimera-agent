"""Stage 1 of `PREREGISTRATION.md` — measure the variance, then say what n stage 2 would need.

This runs **arm A only**. It does not compare anything, because there is nothing to compare to yet:
arm B's tool is deliberately unwritten so its design cannot be tuned to a variance already seen.

Why a pilot at all: a review of this project's five never-run pre-registrations found the same defect
in every one — the design could not detect the effect it was looking for, and nobody checked before
writing it down. `context_curve` demanded disjoint Wilson intervals for a 3pp effect, which never
happens at any affordable n. `role_routing` demanded ten discordant pairs at n=41 for an effect the
literature puts at +2.1pp, which yields about one. Their "inconclusive" branches were the *expected*
outcome, not a risk. So this measures the spread first and reports the required n honestly, including
when the answer is "more than we can afford".

    python bench/edit_tools/run_pilot.py --repeats 3

Reads its numbers from `runs.jsonl`, which now carries `tool_names` per attempt. Before that wire
existed a finished run recorded what it COST and never what it DID, and this bench's primary metric
was unreadable — see `tests/test_attempt_tool_names.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from tasks import PILOT  # noqa: E402

#: Tools that count as an edit. `edit_batch` is arm B's and is listed now, before it exists, so the
#: metric definition is fixed by the pre-registration rather than by whatever arm B turns out to add.
EDIT_TOOLS = {"edit_file", "apply_patch", "write_file", "edit_batch"}


def _dotenv() -> dict[str, str]:
    """The repo `.env`, so a solve run from a temp cwd still finds the provider key."""
    env_file = REPO_ROOT / ".env"
    out: dict[str, str] = {}
    if not env_file.exists():
        return out
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _materialise(task: dict, root: Path) -> None:
    for rel, body in task["files"].items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _run_solve(
    task: dict, ws: Path, home: Path, timeout: int, extra_env: dict[str, str] | None = None
) -> tuple[int, str]:
    """Run arm A once. `home` isolates this run's `runs.jsonl` from every other.

    Isolation via ``CHIMERA_HOME`` rather than a flag: ``solve`` has no ``--runs`` (that belongs to
    a different command) and writes to ``settings.home``. A shared home would also mean every
    repetition appending to one file, and "the last line" would stop meaning "this run".
    """
    verify = f'"{sys.executable}" -m pytest -q {task["test"]}'
    argv = [
        sys.executable, "-m", "chimera.cli.main", "solve", task["prompt"],
        "--workspace", str(ws),
        "--verify", verify,
    ]
    try:
        proc = subprocess.run(
            argv, cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
            env={**os.environ, **_dotenv(), "CHIMERA_HOME": str(home), **(extra_env or {})},
        )
        return proc.returncode, ((proc.stdout or "")[-300:] + (proc.stderr or "")[-300:])
    except subprocess.TimeoutExpired:
        return 124, "solve timed out"


def _verdict(task: dict, ws: Path) -> bool:
    """The authoritative pass/fail: OUR pytest, on a test restored from pristine.

    Restoring the test first is not paranoia about this particular agent — it is the same rule the
    diff gate follows everywhere else in this repo: never accept the thing under test as its own
    witness. An agent that edits the test into passing would otherwise score a clean run.
    """
    (ws / task["test"]).write_text(task["files"][task["test"]], encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", task["test"]],
        cwd=str(ws), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return proc.returncode == 0


def _last_receipt(runs: Path) -> dict:
    if not runs.exists():
        return {}
    lines = [ln for ln in runs.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return json.loads(lines[-1]) if lines else {}


def _measure(receipt: dict) -> tuple[int, int, int]:
    """(edit calls, all tool calls, completion tokens) summed over the run's attempts."""
    attempts = receipt.get("attempts") or []
    names = [n for a in attempts for n in (a.get("tool_names") or [])]
    tokens = sum(int(a.get("completion_tokens") or 0) for a in attempts)
    return sum(1 for n in names if n in EDIT_TOOLS), len(names), tokens


def _required_n(by_task: dict[str, list[int]], effect: float = 2.0) -> dict[str, object]:
    """n per arm for 80% power at the registered effect of 2 fewer edit calls.

    **The variance that matters here is WITHIN a task, not across tasks**, and getting that backwards
    is how this function first shipped. Stage 2 is paired — the same task runs in both arms — so what
    limits power is how much one task's edit count moves between runs, never how much task A differs
    from task B. Across-task spread is dominated by task difficulty, which the pairing already
    cancels; feeding it into a power calculation inflates n by whatever the suite's difficulty range
    happens to be. On the first pilot that was 2.60 against 0.82, and the answer moved from 13 per
    arm to 3 — the difference between "probably not worth it" and "run it this afternoon".

    Pooled within-task sd, then scaled by sqrt(2) for the spread of a *difference* between two
    independent arms. That last step is conservative: any real correlation between the arms on the
    same task (likely — the task is what is hard) shrinks the difference-spread further, so the
    figure errs high, and the direction is stated rather than left for the reader to assume.
    """
    usable = {k: v for k, v in by_task.items() if len(v) > 1}
    if not usable:
        return {"n": None, "why": "unknown — need >= 2 runs of at least one task"}
    within = (sum(statistics.variance(v) for v in usable.values()) / len(usable)) ** 0.5
    if within == 0:
        return {
            "n": 2,
            "sd_within": 0.0,
            "why": (
                "every task returned an identical edit count on every repeat. Low variance is real "
                "power, but zero deserves a second look before it is spent: it can equally mean the "
                "suite is degenerate or the arm is saturated. n=2 is the floor for a paired test, "
                "not a computed figure."
            ),
        }
    sd_diff = within * (2 ** 0.5)
    n = ((1.96 + 0.84) * sd_diff / effect) ** 2
    return {
        "n": max(2, round(n)),
        "sd_within": round(within, 3),
        "sd_of_difference": round(sd_diff, 3),
        "why": (
            f"pooled within-task sd {within:.2f}; difference sd {sd_diff:.2f}; effect {effect} edit "
            f"calls; 80% power; alpha .05 two-sided. Errs HIGH — arm correlation lowers it."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3, help="runs per task (variance needs > 1)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default=str(HERE / "results" / "pilot.json"))
    args = ap.parse_args()

    rows: list[dict] = []
    for task in PILOT:
        for rep in range(args.repeats):
            work = Path(tempfile.mkdtemp(prefix=f"editbench-{task['name']}-"))
            home = work / "home"
            runs = home / "runs.jsonl"
            ws = work / "ws"
            ws.mkdir()
            _materialise(task, ws)
            started = time.time()
            code, tail = _run_solve(task, ws, home, args.timeout)
            passed = _verdict(task, ws)
            edits, calls, tokens = _measure(_last_receipt(runs))
            rows.append({
                "task": task["name"], "family": task["family"], "rep": rep,
                "passed": passed, "exit": code, "edit_calls": edits,
                "tool_calls": calls, "completion_tokens": tokens,
                "seconds": round(time.time() - started, 1), "tail": tail,
            })
            print(f"  {task['name']:<28} rep{rep}  pass={passed!s:<5} "
                  f"edits={edits:<3} calls={calls:<3} tok={tokens}")
            shutil.rmtree(work, ignore_errors=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    solved = [r for r in rows if r["passed"]]
    by_task: dict[str, list[int]] = {}
    for r in solved:
        by_task.setdefault(r["task"], []).append(r["edit_calls"])
    # A task arm A never solved is dropped from the spread and its name published: how many edits a
    # task takes is undefined for a task that does not get done, and the discard rule is in the
    # pre-registration precisely so it cannot be applied after seeing which way it helps.
    unsolved = sorted({r["task"] for r in rows} - set(by_task))
    summary = {
        "arm": "A (today's tool surface)",
        "runs": len(rows),
        "solved": len(solved),
        "excluded_unsolved_tasks": unsolved,
        "edit_calls_by_task": by_task,
        "required_n_for_stage_2": _required_n(by_task),
        "rows": rows,
    }
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    req = summary["required_n_for_stage_2"]
    print(f"\n  solved {len(solved)}/{len(rows)}")
    if unsolved:
        print(f"  excluded (arm A never solved): {', '.join(unsolved)}")
    print(f"  stage 2 would need: n={req.get('n')} per arm — {req.get('why')}")
    print(f"  -> {out}")
    if not solved:
        print("\n  Nothing solved. That is a result about the SUITE or the model, not about the\n"
              "  tools, and stage 2 must not run until it is understood.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
