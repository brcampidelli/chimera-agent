"""Should a gated task route to the cheap tier by default? — the staged run.

Design, margin and adoption criterion are fixed in PREREGISTRATION.md, committed before this file
ran once. Read that first; this is only the apparatus.

**It stops.** Stage 1 runs the SHIPPED model alone over the corpus and reports its pass rate. Above
90% or below 20% the corpus cannot answer the question — a non-inferiority margin satisfied by a
ceiling is satisfied by the ceiling, not by the models — and stage 2 is not paid for. That gate is
here because this project has hit that ceiling four times: `local_lift` at 100% for three frontier
models, GSM8K at 100% oracle, and three attempts at a 40-60% band that landed at 84-92%.

Usage:
    python bench/cost_routing/run_routing.py            # stage 1, then stop
    python bench/cost_routing/run_routing.py --stage2   # the registered run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = HERE / "results"
JOURNAL = RESULTS / "journal.jsonl"

#: The three arms. Only the model differs — same tasks, same fork, same gate, same loop.
ARMS = {
    "A_flash": "openrouter/deepseek/deepseek-v4-flash-0731",
    "B_frontier": "openrouter/x-ai/grok-4.6",
    "C_shipped": "openrouter/deepseek/deepseek-chat-v3.1",
}
SEEDS = (42, 43, 44)

#: Outside this band the corpus decides the answer before the models do. Registered, not tuned.
BAND = (0.20, 0.90)
#: Under this, the run reports the corpus as the limit rather than reporting a delta.
MIN_TASKS = 20

_PY = sys.executable


def prereg_sha() -> str:
    """The design this row was run under, stamped on the row.

    A result read against a design it was not run under is worse than no result, and an edited
    pre-registration is exactly how that happens without anybody lying.
    """
    path = HERE / "PREREGISTRATION.md"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else "MISSING"


def setup(task: dict, root: Path, arm: str, seed: int) -> Path:
    """A clean fork per cell. Two arms sharing a folder would measure who wrote last."""
    ws = root / f"{task['id']}__{arm}__s{seed}"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    for rel, content in (task.get("files") or {}).items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (ws / task["test"]).write_text(task["test_src"], encoding="utf-8")
    return ws


def gate(task: dict, ws: Path) -> bool:
    """The authoritative verdict: run the test ourselves, ignore what solve claimed.

    An arm that self-reports success is an arm grading its own homework.
    """
    proc = subprocess.run(
        [_PY, "-m", "pytest", "-q", task["test"]], cwd=str(ws), capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    return proc.returncode == 0


def solve(task: dict, ws: Path, model: str, seed: int, timeout: int) -> dict:
    """One solve. Returns the outcome AND the evidence needed to tell a crash from a failure."""
    argv = [
        str(Path(_PY).parent / "chimera"), "solve", task["prompt"],
        "--workspace", str(ws),
        "--verify", f'"{_PY}" -m pytest -q {task["test"]}',
        "--model", model, "--max-attempts", "3",
        # Nothing may carry in from the cell before it: the loop is task -> arm -> seed, so without
        # these an early arm teaches a later one on the SAME task and the bias runs with arm order.
        "--no-remember", "--no-collect", "--no-evolve-skills", "--no-skill-cards",
    ]
    began = time.monotonic()
    try:
        proc = subprocess.run(
            argv, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
        code, tail = proc.returncode, ((proc.stdout or "")[-300:] + (proc.stderr or "")[-300:])
    except subprocess.TimeoutExpired:
        code, tail = 124, "timed out"
    return {"exit": code, "tail": tail, "seconds": round(time.monotonic() - began, 1)}


def cost(home: Path, ws: Path, since: str) -> dict:
    """What this cell spent, joined by workspace and SCOPED to this run's start.

    Both halves are load-bearing. The receipt for a CLI solve lives on the attempts of `runs.jsonl`,
    not in `usage.jsonl` — reading the latter returned a confident $0.00 for six paid runs. And the
    workspace path repeats between runs, so the join alone adds the previous run of the same cell:
    that is how a pilot here read 86,983 tokens where the truth was 43,219.
    """
    log = home / "runs.jsonl"
    if not log.is_file():
        return {"prompt": 0, "completion": 0, "usd": None, "known": False}
    needle = ws.name
    prompt = completion = joined = 0
    usd: float | None = 0.0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if needle not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if needle not in str(row.get("workspace", "")) or str(row.get("ts", "")) < since:
            continue
        for a in row.get("attempts") or []:
            joined += 1
            prompt += int(a.get("prompt_tokens") or 0)
            completion += int(a.get("completion_tokens") or 0)
            if a.get("usd") is None:
                usd = None
            elif usd is not None:
                usd += float(a["usd"])
    # Unknown, never zero: a paid cell that reads zero looks exactly like a cheap one, and the
    # per-cell rate is what any projection is built on.
    return {"prompt": prompt, "completion": completion,
            "usd": usd if joined else None, "known": joined > 0}


def cell(task: dict, arm: str, seed: int, root: Path, home: Path, timeout: int) -> dict:
    since = datetime.now(UTC).isoformat()
    ws = setup(task, root, arm, seed)
    ran = solve(task, ws, ARMS[arm], seed, timeout)
    passed = gate(task, ws)
    # A crash and an incapacity produce the same row and only one is evidence. Measured: one arm
    # produced 0 tokens in 21 seconds and no file, and the same model worked when re-probed alone.
    crashed = ran["exit"] not in (0, 1) or ran["exit"] == 124
    return {
        "task": task["id"], "arm": arm, "seed": seed, "passed": passed,
        "crashed": crashed, "exit": ran["exit"], "tail": ran["tail"][-300:],
        "seconds": ran["seconds"], "prereg": prereg_sha(),
        "chimera_version": version("chimera-agent"), "model": ARMS[arm],
        **cost(home, ws, since),
    }


def corpus() -> list[dict]:
    sys.path.insert(0, str(REPO / "bench/local_lift"))
    from tasks import TASKS  # noqa: PLC0415

    return list(TASKS)


def report(rows: list[dict]) -> str:
    from chimera.eval.paired import compare_paired, format_report  # noqa: PLC0415

    live = [r for r in rows if not r["crashed"]]
    dead = len(rows) - len(live)
    out: list[str] = []
    if dead:
        # Named, never dropped in silence: "crashed" and "tested and failed" are different claims.
        out.append(f"excluded {dead} crashed cell(s) from the denominator — see `crashed` in the journal")
    keyed = {(r["task"], r["arm"], r["seed"]): r for r in live}
    pairs = sorted({(r["task"], r["seed"]) for r in live})

    def outcomes(arm: str) -> list[bool] | None:
        got = [keyed.get((t, arm, s)) for t, s in pairs]
        return None if any(g is None for g in got) else [bool(g["passed"]) for g in got if g]

    for base, treat in (("B_frontier", "A_flash"), ("C_shipped", "A_flash")):
        b, t = outcomes(base), outcomes(treat)
        if b is None or t is None:
            out.append(f"{treat} vs {base}: incomplete — some cells did not run")
            continue
        out.append(format_report(compare_paired(b, t, baseline_name=base, treatment_name=treat)))
    for arm in ARMS:
        cells = [r for r in live if r["arm"] == arm]
        if not cells:
            continue
        tok = sum(int(r["prompt"] or 0) + int(r["completion"] or 0) for r in cells)
        money = sum(r["usd"] or 0.0 for r in cells)
        floor = " (floor)" if any(r["usd"] is None for r in cells) else ""
        out.append(f"  {arm:<12} {len(cells)} cells, {tok:,} tokens, ${money:.4f}{floor}")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage2", action="store_true", help="Run the registered three-arm run.")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--sample", type=int, default=30)
    args = parser.parse_args()

    home = REPO / ".chimera"
    RESULTS.mkdir(parents=True, exist_ok=True)
    work = RESULTS / "workspaces"
    work.mkdir(exist_ok=True)
    tasks = corpus()[: args.sample]
    print(f"prereg={prereg_sha()}  tasks={len(tasks)}  band={BAND}  min_tasks={MIN_TASKS}")

    rows: list[dict] = []
    with JOURNAL.open("a", encoding="utf-8") as journal:
        if not args.stage2:
            print("\nstage 1: the SHIPPED model alone, one seed — does this corpus discriminate?")
            for task in tasks:
                row = cell(task, "C_shipped", SEEDS[0], work, home, args.timeout)
                rows.append(row)
                journal.write(json.dumps(row, ensure_ascii=False) + "\n")
                journal.flush()
                print(f"  {task['id']:<28} {'PASS' if row['passed'] else 'fail'}"
                      f"{'  CRASHED' if row['crashed'] else ''}  {row['seconds']:>6.1f}s")
            live = [r for r in rows if not r["crashed"]]
            rate = sum(r["passed"] for r in live) / len(live) if live else 0.0
            print(f"\nshipped model passes {rate:.1%} of {len(live)} live cells")
            if not BAND[0] <= rate <= BAND[1]:
                print("=" * 78)
                print(f"STOP, by the rule fixed in the pre-registration: {rate:.1%} is outside "
                      f"{BAND[0]:.0%}-{BAND[1]:.0%}.")
                print("A non-inferiority margin satisfied by a ceiling is satisfied by the ceiling,")
                print("not by the models. Stage 2 is not run.")
                return
            discriminating = [r["task"] for r in live if not r["passed"]]
            if len(discriminating) < MIN_TASKS:
                print("=" * 78)
                print(f"STOP: only {len(discriminating)} task(s) the shipped model fails, below "
                      f"{MIN_TASKS}.")
                print("The corpus, not the models, is the limit — and that is the finding.")
                return
            print("=" * 78)
            print(f"In band. Re-run with --stage2 to pay for {len(ARMS) * len(SEEDS) * len(tasks)} "
                  "cells.")
            return

        for task in tasks:
            for arm in ARMS:
                for seed in SEEDS:
                    row = cell(task, arm, seed, work, home, args.timeout)
                    rows.append(row)
                    journal.write(json.dumps(row, ensure_ascii=False) + "\n")
                    journal.flush()
                    print(f"  {task['id']:<24} {arm:<12} s{seed} "
                          f"{'PASS' if row['passed'] else 'fail'}"
                          f"{'  CRASHED' if row['crashed'] else ''}")
    print("\n" + report(rows))


if __name__ == "__main__":
    main()
