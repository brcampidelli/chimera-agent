"""Does the fusion panel beat spending the same budget on one model? — the paired run.

Design, thresholds and the refutation criterion are fixed in PREREGISTRATION.md, which was committed
before this file ran once. Read that first; this is only the apparatus.

Three arms over the SAME 100 tasks, the same workspace fork and the same pytest gate:

    A  fusion   the shipped panel -> judge -> synthesiser, one attempt
    B  repeat   the best single model, three samples, the gate keeps the first that passes
    C  single   the best single model, one sample

C is the control that separates *more diversity* from *more computation*. Without it a win for A is
unattributable, which is the mistake the MoA paper made and Self-MoA corrected.

**It stops.** The first invocation runs a PILOT — a few tasks across all arms and seeds — prints the
MEASURED tokens and dollars, and exits. The full 900-solve run needs `--full` and a person deciding.
The alternative would be an estimate, and there is nothing to estimate from: the prior bench's
journal records outcomes and not cost. Measuring five is cheaper than being wrong about nine hundred.

Usage:
    uv run --no-sync python bench/fusion_paired/run_paired.py            # pilot, then stop
    uv run --no-sync python bench/fusion_paired/run_paired.py --full     # the registered run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / "local_lift"))
from tasks import TASKS  # noqa: E402

from chimera.eval.paired import compare_paired, format_report  # noqa: E402
from chimera.eval.selftest import TaskCheck, assert_discriminating  # noqa: E402

RESULTS = HERE / "results"
JOURNAL = RESULTS / "journal.jsonl"
_PY = sys.executable
_CHIMERA = str(Path(_PY).parent / "chimera")

#: Fixed in the pre-registration. Two alert, three decide.
SEEDS = (42, 43, 44)

#: How many tasks the pilot pays for before it stops and asks. Enough to measure the per-solve cost
#: of the most expensive arm, few enough that being wrong about it costs pocket change.
PILOT_TASKS = 5

#: The arms, and the ONLY thing that differs between them.
#:
#: `--samples` does not exist as a solve flag; arm B is N independent solves of the same task from
#: the same fork, and the gate keeps the first that passes. Written as a count here so the runner,
#: not a flag, is what makes the arms comparable.
ARMS: dict[str, dict[str, object]] = {
    "A_fusion": {"flags": ["--fuse", "--max-attempts", "1"], "samples": 1},
    "B_repeat": {"flags": ["--max-attempts", "1"], "samples": 3},
    "C_single": {"flags": ["--max-attempts", "1"], "samples": 1},
}


def _load_dotenv() -> dict[str, str]:
    """Parse the repo .env so the solve subprocess (run from a temp cwd) keeps the provider key."""
    env: dict[str, str] = {}
    dotenv = REPO_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


_DOTENV = _load_dotenv()


def prereg_sha() -> str:
    """The design this row was run under, stamped on the row.

    A result read against a design it was not run under is worse than no result, and an edited
    pre-registration is exactly how that happens without anybody lying.
    """
    path = HERE / "PREREGISTRATION.md"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else "MISSING"


def setup_workspace(task: dict, root: Path) -> Path:
    ws = root / task["id"]
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    for rel, content in task.get("files", {}).items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (ws / task["test"]).write_text(task["test_src"], encoding="utf-8")
    return ws


def independent_pytest(task: dict, ws: Path) -> bool:
    """The authoritative verdict: run the test ourselves, ignore what solve claimed.

    The whole comparison rests on this line. An arm that self-reports success is an arm grading its
    own homework, and the fusion arm has a judge inside it whose entire job is to say things went
    well.
    """
    proc = subprocess.run(
        [_PY, "-m", "pytest", "-q", task["test"]],
        cwd=str(ws), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return proc.returncode == 0


def run_solve(task: dict, ws: Path, flags: list[str], seed: int, timeout: int) -> dict:
    """One solve. Returns the outcome plus what it cost, read from the run receipt."""
    verify = f'"{_PY}" -m pytest -q {task["test"]}'
    argv = [
        _CHIMERA, "solve", task["prompt"],
        "--workspace", str(ws),
        "--verify", verify,
        *flags,
    ]
    env = {**os.environ, **_DOTENV, "CHIMERA_SEED": str(seed), "PYTHONHASHSEED": str(seed)}
    began = time.monotonic()
    try:
        proc = subprocess.run(
            argv, cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False, env=env,
        )
        code, tail = proc.returncode, ((proc.stdout or "")[-400:] + (proc.stderr or "")[-400:])
    except subprocess.TimeoutExpired:
        code, tail = 124, "solve timed out"
    return {"exit": code, "tail": tail, "seconds": round(time.monotonic() - began, 1)}


def read_cost(home: Path, since: float) -> dict[str, float | None]:
    """Tokens and dollars this solve added to the usage log.

    Read from the receipt rather than parsed from stdout: the log is what the Cost screen reads, and
    a number taken from a different place than the product's own would be a second accounting.

    ``usd`` is None when ANY row in the window is unpriced — the all-or-nothing rule this project
    uses everywhere. A partial sum presented as the total flatters whichever arm used the unpriced
    model, and here that would be the fusion panel.
    """
    log = home / "usage.jsonl"
    if not log.is_file():
        return {"prompt_tokens": 0, "completion_tokens": 0, "usd": None}
    prompt = completion = 0
    usd: float | None = 0.0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        stamp = row.get("ts") or ""
        if not stamp or stamp < str(since):
            continue
        prompt += int(row.get("prompt_tokens") or 0)
        completion += int(row.get("completion_tokens") or 0)
        if row.get("usd") is None:
            usd = None
        elif usd is not None:
            usd += float(row["usd"])
    return {"prompt_tokens": prompt, "completion_tokens": completion, "usd": usd}


def run_cell(task: dict, arm: str, seed: int, root: Path, timeout: int, home: Path) -> dict:
    """One (task, arm, seed). Arm B is N solves from the SAME fork; the gate keeps the first pass.

    Each sample starts from a fresh workspace on purpose. Letting sample 2 inherit sample 1's edits
    would make B a three-attempt loop, which is a different experiment — and one this project has
    already run under the name `local_lift`.
    """
    spec = ARMS[arm]
    flags = list(spec["flags"])  # type: ignore[arg-type]
    samples = int(spec["samples"])  # type: ignore[arg-type]
    started = time.time()
    attempts: list[dict] = []
    passed = False
    for index in range(samples):
        ws = setup_workspace(task, root / f"{arm}_s{seed}_{index}")
        attempts.append(run_solve(task, ws, flags, seed + index, timeout))
        if independent_pytest(task, ws):
            passed = True
            break  # the gate keeps the first that passes; the rest are not paid for
    return {
        "task": task["id"],
        "arm": arm,
        "seed": seed,
        "passed": passed,
        "samples_paid": len(attempts),
        "seconds": sum(a["seconds"] for a in attempts),
        **read_cost(home, started),
        "prereg": prereg_sha(),
        "chimera_version": version("chimera-agent"),
        "model": os.environ.get("CHIMERA_DEFAULT_MODEL", "?"),
    }


def report(rows: list[dict]) -> str:
    """The three comparisons, each paired per (task, seed), plus the token ratio the criterion needs."""
    out: list[str] = []
    keyed = {(r["task"], r["arm"], r["seed"]): r for r in rows}
    tasks = sorted({r["task"] for r in rows})
    seeds = sorted({r["seed"] for r in rows})
    pairs = [(t, s) for t in tasks for s in seeds]

    def outcomes(arm: str) -> list[bool] | None:
        got = [keyed.get((t, arm, s)) for t, s in pairs]
        return None if any(g is None for g in got) else [bool(g["passed"]) for g in got if g]

    def tokens(arm: str) -> int:
        return sum(
            int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0)
            for r in rows if r["arm"] == arm
        )

    for base, treat in (("B_repeat", "A_fusion"), ("C_single", "A_fusion"), ("C_single", "B_repeat")):
        b, t = outcomes(base), outcomes(treat)
        if b is None or t is None:
            out.append(f"{treat} vs {base}: incomplete — some cells did not run")
            continue
        out.append(format_report(compare_paired(b, t, baseline_name=base, treatment_name=treat)))
        base_tokens, treat_tokens = tokens(base), tokens(treat)
        ratio = (treat_tokens / base_tokens) if base_tokens else float("inf")
        # Printed beside every delta, never under it: criterion 3 of the pre-registration is a cost
        # ceiling, and a lift reported without its price cannot be checked against it.
        out.append(f"  tokens {treat}={treat_tokens:,} {base}={base_tokens:,} ratio={ratio:.2f}\n")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Run the registered 900 solves.")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--pilot-tasks", type=int, default=PILOT_TASKS)
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    home = Path(os.environ.get("CHIMERA_HOME") or (Path.home() / ".chimera"))
    tasks = TASKS if args.full else TASKS[: args.pilot_tasks]
    work = RESULTS / "workspaces"
    work.mkdir(exist_ok=True)

    print(f"prereg={prereg_sha()}  tasks={len(tasks)}  arms={list(ARMS)}  seeds={SEEDS}")
    print(f"cells={len(tasks) * len(ARMS) * len(SEEDS)}  ({'FULL' if args.full else 'PILOT'})")

    # Prove the apparatus before paying for the phenomenon. A task whose test already passes on the
    # untouched workspace scores a hit for an arm that did nothing, and nothing downstream would
    # ever say so. Costs no model call and aborts before the first one.
    assert_discriminating([
        TaskCheck(
            task_id=str(task["id"]),
            setup=lambda task=task: setup_workspace(task, work / "_selftest"),
            verify=f'"{_PY}" -m pytest -q {task["test"]}',
        )
        for task in tasks
    ])
    print("apparatus: every task's test fails on the untouched workspace")

    rows: list[dict] = []
    with JOURNAL.open("a", encoding="utf-8") as journal:
        for task in tasks:
            for seed in SEEDS:
                for arm in ARMS:
                    row = run_cell(task, arm, seed, work, args.timeout, home)
                    rows.append(row)
                    journal.write(json.dumps(row, ensure_ascii=False) + "\n")
                    journal.flush()
                    print(f"  {task['id']:<28} {arm:<9} seed={seed} "
                          f"{'PASS' if row['passed'] else 'fail'} "
                          f"{row['seconds']:>6.1f}s  ${row['usd'] if row['usd'] is not None else '?'}")

    print("\n" + report(rows))

    if not args.full:
        spent = sum(r["usd"] or 0.0 for r in rows)
        unpriced = any(r["usd"] is None for r in rows)
        per_cell = spent / len(rows) if rows else 0.0
        full_cells = len(TASKS) * len(ARMS) * len(SEEDS)
        print("=" * 78)
        print(f"PILOT ONLY. {len(rows)} cells cost ${spent:.2f}"
              + (" (some rows unpriced — treat the total as a floor)" if unpriced else ""))
        print(f"The registered run is {full_cells} cells "
              f"→ roughly ${per_cell * full_cells:.2f} at the measured rate.")
        print("Nothing here is a result. Re-run with --full to pay for one.")


if __name__ == "__main__":
    main()
