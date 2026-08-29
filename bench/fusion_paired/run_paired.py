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
from datetime import UTC, datetime
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

#: What no arm may carry in from a previous cell. Added in ADDENDUM-01, and missing before it: the
#: loop is `for task -> for seed -> for arm`, so arm A would solve a task, write a long-term memory
#: fact and possibly a learned skill, and arms B and C would then solve THE SAME TASK with that in
#: reach. The contamination runs with the arm order, so it credits whichever arm ran last, and
#: nothing in the recorded rows would have said so. Every other runner in `bench/local_lift` has
#: carried this set since it was written; this one did not.
_HYGIENE = ["--no-remember", "--no-collect", "--no-evolve-skills", "--no-skill-cards"]

#: The single model arms B and C spend their budget on. DECLARED, not measured — it is the model the
#: shipped configuration already trusts to write the final answer (`_DEFAULT_SYNTHESIZER`) and is
#: `_DEFAULT_PANEL[0]`. ADDENDUM-01 states the direction of the residual error: if this is not the
#: strongest panel member on this corpus, arm B is understated and the bias favours FUSION, so a
#: null survives the doubt and a win for A is the outcome that would need the choice measured first.
#:
#: Without this pin both arms inherited `settings.default_model` — `deepseek-chat-v3.1`, a cheap
#: model — against a frontier panel, which would have made criterion 1 a measurement of model tier
#: and criterion 3 a comparison between price classes.
SINGLE_MODEL = "openrouter/anthropic/claude-opus-5"

#: Screened, never selected. A panel member that solves ZERO pilot tasks is excluded from being the
#: single model, because that is a broken ruler rather than a weak model. A good showing may NOT
#: promote one: five tasks at one seed cannot resolve a real difference, and letting it try would be
#: a forking path. The rule is absolute and predates the numbers.
SCREEN_MODELS = (
    "openrouter/anthropic/claude-opus-5",
    "openrouter/openai/gpt-5.5",
    "openrouter/google/gemini-3.1-pro-preview",
)

#: The arms, and the ONLY thing that differs between them.
#:
#: `--samples` does not exist as a solve flag; arm B is N independent solves of the same task from
#: the same fork, and the gate keeps the first that passes. Written as a count here so the runner,
#: not a flag, is what makes the arms comparable.
#:
#: Arm A pins no model on purpose. `--fuse` routes deep-reasoning turns through the panel and leaves
#: the rest on `default_model`; that mixed route IS the shipped product, and pinning it would
#: measure something no user gets. The budgets are held comparable by criterion 3's token ratio,
#: which is measured, rather than by making the flags look symmetric.
ARMS: dict[str, dict[str, object]] = {
    "A_fusion": {"flags": ["--fuse", "--max-attempts", "1", *_HYGIENE], "samples": 1},
    "B_repeat": {
        "flags": ["--model", SINGLE_MODEL, "--max-attempts", "1", *_HYGIENE], "samples": 3
    },
    "C_single": {
        "flags": ["--model", SINGLE_MODEL, "--max-attempts", "1", *_HYGIENE], "samples": 1
    },
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


def addendum_sha() -> str:
    """Stamped beside the pre-registration's. The addendum changed the apparatus, not the design, and
    a row that does not say which apparatus produced it cannot be compared with one that does."""
    path = HERE / "ADDENDUM-01.md"
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


def read_cost(home: Path, workspace: Path, since: str = "") -> dict[str, float | None]:
    """What this cell spent, joined by WORKSPACE rather than by a window of time.

    Two things were wrong before, and the smoke run made both visible at once by printing
    `0 tokens, $0.00, ratio=inf` for six runs that cost real money (ADDENDUM-02):

    - it read `usage.jsonl`, which only the API layer writes. A CLI solve records nothing there; its
      receipt lives on the ATTEMPTS of `runs.jsonl`, one level below where a reader looks.
    - it resolved `~/.chimera`, but `settings.home` defaults to the RELATIVE `Path(".chimera")`, so
      a solve run from the repo root writes to the repo, not to the user's home.

    The window itself was the third: it compared an ISO timestamp against `str(time.time())` as
    strings. The workspace path is unique per cell and is recorded on the row, so the join is exact
    and no clock is involved.

    ``since`` is the ISO-UTC instant this run began, and it is not optional in practice. The
    workspace path repeats between runs — the pilot reuses `screen_<model>/<task>` — so the join
    alone silently ADDS every earlier run of the same cell. It was caught by the pilot itself: a
    screen cell read 86,983 prompt tokens where the smoke run of the same cell had measured 43,219,
    which is the smoke plus the pilot. ISO-UTC timestamps compare correctly as strings, and the
    earlier version's bug was comparing one against ``str(time.time())``, not the comparison itself.

    ``usd`` follows the all-or-nothing rule and is None when any joined attempt is unpriced — here
    that is the frontier models, which the local catalogue does not price. ``tokens_known`` is False
    when the join found nothing at all: a paid cell that reads zero is the failure this docstring
    describes, and the caller refuses to estimate from it rather than reporting a confident zero.
    """
    log = home / "runs.jsonl"
    if not log.is_file():
        return {"prompt_tokens": 0, "completion_tokens": 0, "usd": None, "tokens_known": False}
    needle = workspace.as_posix().split("workspaces/")[-1]
    prompt = completion = joined = 0
    usd: float | None = 0.0
    models: set[str] = set()
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if needle not in line:
            continue  # cheap prefilter; the json parse below is what decides
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get("workspace", "")).replace(chr(92), "/").split("workspaces/")[-1] != needle:
            continue
        if since and str(row.get("ts", "")) < since:
            continue  # an earlier run of the SAME cell: its cost is not this run's
        for attempt in row.get("attempts") or []:
            joined += 1
            prompt += int(attempt.get("prompt_tokens") or 0)
            completion += int(attempt.get("completion_tokens") or 0)
            models.add(str(attempt.get("model") or "?"))
            if attempt.get("usd") is None:
                usd = None
            elif usd is not None:
                usd += float(attempt["usd"])
    return {
        "prompt_tokens": prompt, "completion_tokens": completion,
        "usd": usd if joined else None, "tokens_known": joined > 0,
        "worker_models": sorted(models),
    }


def _sum_costs(home: Path, forks: list[Path], since: str) -> dict:
    """Arm B pays for every sample it took, so a cell's cost is the sum over its forks."""
    parts = [read_cost(home, fork, since) for fork in forks]
    usd: float | None = 0.0
    for part in parts:
        if part["usd"] is None:
            usd = None
        elif usd is not None:
            usd += float(part["usd"])
    models: set[str] = set()
    for part in parts:
        models.update(part.get("worker_models") or [])
    return {
        "prompt_tokens": sum(int(p["prompt_tokens"] or 0) for p in parts),
        "completion_tokens": sum(int(p["completion_tokens"] or 0) for p in parts),
        "usd": usd,
        "tokens_known": all(p["tokens_known"] for p in parts),
        "worker_models": sorted(models),
    }


def run_cell(
    task: dict, arm: str, seed: int, root: Path, timeout: int, home: Path, since: str
) -> dict:
    """One (task, arm, seed). Arm B is N solves from the SAME fork; the gate keeps the first pass.

    Each sample starts from a fresh workspace on purpose. Letting sample 2 inherit sample 1's edits
    would make B a three-attempt loop, which is a different experiment — and one this project has
    already run under the name `local_lift`.
    """
    spec = ARMS[arm]
    flags = list(spec["flags"])  # type: ignore[arg-type]
    samples = int(spec["samples"])  # type: ignore[arg-type]
    attempts: list[dict] = []
    forks: list[Path] = []
    passed = False
    for index in range(samples):
        ws = setup_workspace(task, root / f"{arm}_s{seed}_{index}")
        forks.append(ws)
        attempts.append(run_solve(task, ws, flags, seed + index, timeout))
        if independent_pytest(task, ws):
            passed = True
            break  # the gate keeps the first that passes; the rest are not paid for
    cost = _sum_costs(home, forks, since)
    return {
        "task": task["id"],
        "arm": arm,
        "seed": seed,
        "passed": passed,
        "samples_paid": len(attempts),
        "seconds": sum(a["seconds"] for a in attempts),
        **cost,
        "prereg": prereg_sha(),
        "addendum": addendum_sha(),
        "chimera_version": version("chimera-agent"),
        "model": SINGLE_MODEL if arm != "A_fusion" else "shipped-fusion-route",
    }


def run_screen(
    tasks: list[dict], seed: int, root: Path, timeout: int, home: Path, journal, since: str
) -> dict[str, int]:
    """The gross-failure screen from ADDENDUM-01: one pass of arm C per panel member.

    It may EXCLUDE and may never PROMOTE. A member that solves zero tasks is a broken ruler rather
    than a weak model — the failure `§2e` describes, where a harness written against one model stops
    reporting the moment the model changes — and the experiment needs to know that before 900 cells
    depend on it. A good showing may not pick the model: five tasks at one seed cannot resolve a real
    difference, and letting it try would turn a declaration into a forking path.
    """
    scored: dict[str, int] = {}
    for model in SCREEN_MODELS:
        short = model.split("/")[-1]
        flags = ["--model", model, "--max-attempts", "1", *_HYGIENE]
        hits = 0
        for task in tasks:
            ws = setup_workspace(task, root / f"screen_{short}")
            outcome = run_solve(task, ws, flags, seed, timeout)
            ok = independent_pytest(task, ws)
            hits += int(ok)
            row = {
                "task": task["id"], "arm": f"screen:{model}", "seed": seed, "passed": ok,
                "samples_paid": 1, "seconds": outcome["seconds"], **read_cost(home, ws, since),
                "prereg": prereg_sha(), "chimera_version": version("chimera-agent"), "model": model,
            }
            journal.write(json.dumps(row, ensure_ascii=False) + "\n")
            journal.flush()
            print(f"  {task['id']:<28} screen {short:<26} "
                  f"{'PASS' if ok else 'fail'} {outcome['seconds']:>6.1f}s")
        scored[model] = hits
        print(f"  -> {short}: {hits}/{len(tasks)}")
    return scored


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
        out.append(f"  tokens {treat}={treat_tokens:,} {base}={base_tokens:,} ratio={ratio:.2f}")
        if base == "C_single" and treat == "A_fusion":
            # ADDENDUM-02: the same number that serves as criterion 3's price ceiling is also the
            # proof the intervention ACTED. Arm A adds a fused planning turn over arm C and nothing
            # else; if the two spend the same tokens, fusion did not fire and every comparison in
            # this run is VOID rather than null. "On and inert" reads exactly like "did not help".
            if 0.90 <= ratio <= 1.10:
                out.append("  ACTIVATION: A and C spent the same tokens (ratio "
                           f"{ratio:.2f}). Fusion did not act — this run is void, not null.")
            else:
                out.append(f"  activation: fusion moved the spend by {(ratio - 1) * 100:+.0f}%")
        out.append("")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Run the registered 900 solves.")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--pilot-tasks", type=int, default=PILOT_TASKS)
    # One seed in the pilot. Seeds separate a difference from a variance in the RESULT; the pilot is
    # not a result and says so in its own last line. Three would triple the bill for nothing.
    parser.add_argument("--pilot-seeds", type=int, default=1)
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    # `settings.home` defaults to the RELATIVE `Path(".chimera")` and a solve runs with cwd=repo
    # root, so the receipts land in the REPO, not in the user's home. Reading `~/.chimera` is what
    # made the smoke run report $0.00 for six paid solves.
    raw = os.environ.get("CHIMERA_HOME")
    home = Path(raw) if raw else REPO_ROOT / ".chimera"
    if not home.is_absolute():
        home = REPO_ROOT / home
    tasks = TASKS if args.full else TASKS[: args.pilot_tasks]
    seeds = SEEDS if args.full else SEEDS[: max(1, args.pilot_seeds)]
    work = RESULTS / "workspaces"
    work.mkdir(exist_ok=True)

    print(f"prereg={prereg_sha()}  addendum={addendum_sha()}")
    print(f"tasks={len(tasks)}  arms={list(ARMS)}  seeds={seeds}  single={SINGLE_MODEL}")
    print(f"cells={len(tasks) * len(ARMS) * len(seeds)}  ({'FULL' if args.full else 'PILOT'})")

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

    # The instant this run began, in the same ISO-UTC shape the receipts carry. Every cost join is
    # scoped by it, because the workspace path repeats between runs and the join alone would add the
    # previous run of the same cell to this one.
    since = datetime.now(UTC).isoformat()

    rows: list[dict] = []
    with JOURNAL.open("a", encoding="utf-8") as journal:
        if not args.full:
            print("\nscreen (ADDENDUM-01): one pass of arm C per panel member")
            scored = run_screen(tasks, seeds[0], work, args.timeout, home, journal, since)
            dead = [m for m, hits in scored.items() if hits == 0]
            if SINGLE_MODEL in dead:
                print(f"\nSTOP: {SINGLE_MODEL} solved 0/{len(tasks)}. That is a broken ruler, not a "
                      "weak model, and arms B and C would measure it instead of the question.")
                raise SystemExit(2)
            if dead:
                print(f"  excluded from ever being the single model: {dead}")

        for task in tasks:
            for seed in seeds:
                for arm in ARMS:
                    row = run_cell(task, arm, seed, work, args.timeout, home, since)
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
        for arm in ARMS:
            cells = [r for r in rows if r["arm"] == arm]
            if not cells:
                continue
            tok = sum(int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0) for r in cells)
            paid = sum(int(r["samples_paid"]) for r in cells)
            money = sum(r["usd"] or 0.0 for r in cells)
            print(f"  {arm:<9} {len(cells)} cells, {paid} solves, {tok:,} tokens, ${money:.2f}"
                  + (" (floor)" if any(r["usd"] is None for r in cells) else ""))
        blind = [r["arm"] for r in rows if not r.get("tokens_known", True)]
        full_cells = len(TASKS) * len(ARMS) * len(SEEDS)
        # Per-cell rate from the ARMS rows only. The screen is pilot-only scaffolding and charging
        # the full run for it would overstate the estimate by roughly half.
        print("=" * 78)
        print(f"PILOT ONLY. {len(rows)} cells cost ${spent:.2f}"
              + (" (some rows unpriced — treat the total as a floor)" if unpriced else ""))
        if blind:
            # A cell that read nothing is not a cheap cell. Estimating from it would repeat, one
            # level up, the confident zero that ADDENDUM-02 exists because of.
            print(f"NO ESTIMATE: {len(blind)} cells joined no receipt at all ({sorted(set(blind))}). "
                  "Their cost is unknown, not zero, and a rate built on them would understate the "
                  "registered run by however much they actually cost.")
        else:
            print(f"The registered run is {full_cells} cells "
                  f"→ roughly ${per_cell * full_cells:.2f} at the measured rate.")
        print("Nothing here is a result. Re-run with --full to pay for one.")


if __name__ == "__main__":
    main()
