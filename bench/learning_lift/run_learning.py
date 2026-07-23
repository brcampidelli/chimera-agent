"""Does accumulated learning actually help? — the symmetric half of the continuous-evolution bench.

`chimera/eval/continuous.py` measures whether performance *holds* across chained tasks. Nothing
measured whether it *improves*, and the flagship weak-model-lift bench disables learning in both arms
(`--no-remember --no-collect --no-evolve-skills`), so it deliberately says nothing about accumulation.

This runs one committed same-family suite twice, in the same committed order (`BENCH_SUITE=hard`, the
40 tasks authored for run 2, is the default; `fix` reproduces run 1):

  cold      — learning off, and a FRESH agent home per task: nothing survives from task to task
  learning  — learning on, and ONE agent home for the whole sequence: skills and memory accumulate

and reports a difference-in-differences, because arm `cold`'s own first-half/second-half change is the
drift caused by task ordering and noise. Subtracting it is what isolates the part attributable to
accumulation.

Run 3 (LEARNING_ROADMAP.md P1+P5): the learn->use loop is CONNECTED by default — the learning arm now
carries `--playbook` (injects curated cross-task strategy unconditionally) and `--skill-cards` (reads
learned skill cards back into context). Run 2 minted 39 skills and injected zero, so its DiD measured
a loop with the wire cut; run 3 measures it connected. `BENCH_CONNECT=0` reproduces run 2's
disconnected baseline. Per-card attribution (uses/successes) is logged so a null tells 'never
retrieved' apart from 'retrieved but did not transfer'.

Run 4 (`BENCH_SEEDS=N`): run 3 showed the DiD stayed ~0 while learning sat +10pp above cold in BOTH
halves — a LEVEL shift the slope-based DiD subtracts to zero. So the primary meter is now a POOLED
PAIRED estimate (McNemar + Wilson on discordant pairs, chimera/eval/paired): both arms solve the SAME
task from an identical workspace, so per-task pairs across N seeds pool into one difference CI that
CAN see a constant offset. The DiD is still reported per seed for continuity. P3 (error-seeded
playbook curation) rides in the product default and is measured together with P1+P5 here.

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
# Multiple seeds (run 4): the whole cold+learning paired suite, repeated N times. The model is
# sampled with temperature>0, so each repetition is an independent draw (runs 2 vs 3 already differ
# on identical tasks) — pooling the per-task paired pairs across seeds is what buys the power the
# single-seed DiD lacked. A level-shift benefit (learning uniformly above cold) is invisible to the
# slope-based DiD but shows in the pooled paired delta + its CI (McNemar/Wilson, chimera/eval/paired).
_SEEDS = max(1, int(os.environ.get("BENCH_SEEDS", "1")))

def _suite() -> tuple[str, list[dict]]:
    """The committed suite. Order is committed and is NOT re-shuffled — see PREREGISTRATION.md.

    ``fix``  — run 1: the one family in `local_lift` with shared structure. Kept so run 1 stays
               reproducible; its control arm hit the ceiling (15/15 first half), which is why run 2
               exists at all.
    ``hard`` — run 2: 40 tasks authored for this bench to a difficulty spec fixed before authoring.
    """
    name = os.environ.get("BENCH_SUITE", "hard").lower()
    if name == "fix":
        return name, [t for t in TASKS if str(t["id"]).startswith("fix_")]
    if name == "hard":
        from tasks_hard_fix import HARD_FIX_TASKS

        return name, list(HARD_FIX_TASKS)
    raise SystemExit(f"unknown BENCH_SUITE={name!r} (expected 'hard' or 'fix')")


SUITE_NAME, SUITE = _suite()
HALF = len(SUITE) // 2

# Connect the learn->use loop (run 3 / LEARNING_ROADMAP.md P1+P5). The grounded study found the
# machinery is write-only by DEFAULT: skill cards inject only when settings.skill_cards is true (off,
# config.py:199) and the ACE playbook only with --playbook — so run 2 minted 39 skills and injected
# ZERO. These two flags close the loop: --playbook injects curated cross-task strategy unconditionally
# (no lexical gate), --skill-cards reads learned skill cards back into context. BENCH_CONNECT=0
# reproduces the run-2 disconnected baseline from the same code.
_CONNECT = os.environ.get("BENCH_CONNECT", "1").strip().lower() not in ("0", "false", "no", "")
_SCAFFOLD = ["--repo-map", "--progress-ledger", "--checklist", "--replan", "--max-attempts", "3"]
# The read flags go on the LEARNING arm only. On `cold` they would be pure no-ops (its fresh
# home-per-task carries no playbook/skills to inject) AND --playbook would fire a wasted curation call
# on all 40 cold tasks. Because the flags can only matter THROUGH survived state — of which cold has
# none — putting them on learning alone keeps the contrast exactly "does accumulated state help?"
# while avoiding that cost. (Cold stays the true no-learning control: no write, no carry, no read.)
_LEARN_CONNECT = ["--playbook", "--skill-cards"] if _CONNECT else []
_ARMS = {
    # The ONLY operative difference is whether learned state survives (and is read) between tasks.
    "cold": [*_SCAFFOLD, "--no-remember", "--no-collect", "--no-evolve-skills"],
    "learning": [*_SCAFFOLD, *_LEARN_CONNECT],
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


def _accumulation(home: Path) -> dict[str, int]:
    """What the learning arm has accumulated so far — the accumulation curve (P1 how-to-measure).

    Reads the persisted stores the loop writes: retrievable skills (active+provisional) and active
    ACE-playbook bullets. Best-effort: a missing/odd file counts as zero, never crashes the run.
    """
    skills = 0
    store = home / "skills.json"
    if store.exists():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            data = json.loads(store.read_text(encoding="utf-8"))
            if isinstance(data, list):
                skills = sum(1 for s in data if isinstance(s, dict)
                             and str(s.get("status", "active")) in ("active", "provisional"))
    bullets = 0
    pb = home / "playbook.json"
    if pb.exists():
        with contextlib.suppress(Exception):
            from chimera.evolution.playbook import Playbook

            bullets = len(Playbook.from_dict(json.loads(pb.read_text(encoding="utf-8"))).active())
    return {"skills_retrievable": skills, "playbook_bullets": bullets}


def _skill_stats(home: Path) -> list[dict[str, object]]:
    """Per-card attribution (P5): name/uses/successes/rate. uses>0 means the card actually reached a
    prompt (credited only on verified, diff-productive runs), so uses==0 across all skills means the
    learn->use loop is still disconnected — the diagnostic that tells a null 'never retrieved' apart
    from 'retrieved but did not transfer'."""
    if not (home / "skills.json").exists():
        return []
    with contextlib.suppress(Exception):
        from chimera.evolution.skill_store import SkillStore

        return SkillStore(home / "skills.json").stats()
    return []


def _run_arm(arm: str, seed: int, root: Path, homes: Path, tampered: set[str],
             accum: list[dict[str, object]]) -> list[bool]:
    """Run the whole committed sequence, in order, for one arm of one seed."""
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
        # Snapshot the learning arm's accumulated state AFTER each task (cold carries nothing).
        if arm == "learning":
            accum.append({"seed": seed, "i": index, "pass": ok, **_accumulation(home)})
        half = "1st" if index <= HALF else "2nd"
        tag = f"s{seed}" if _SEEDS > 1 else ""
        print(f"  [{arm:8}{tag:>3}] {index:2}/{len(SUITE)} {half} {str(task['id']):<22} {'PASS' if ok else 'fail'}", flush=True)
    return results


def _rate(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def _did(cold: list[bool], learning: list[bool]) -> float:
    """Difference-in-differences for one seed: (learn 2nd-1st) − (cold 2nd-1st)."""
    lh = (_rate(learning[:HALF]), _rate(learning[HALF:]))
    ch = (_rate(cold[:HALF]), _rate(cold[HALF:]))
    return (lh[1] - lh[0]) - (ch[1] - ch[0])


def main() -> None:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    _OUT.mkdir(parents=True, exist_ok=True)
    print(f"learning-lift · suite={SUITE_NAME} · learn->use {'CONNECTED' if _CONNECT else 'DISCONNECTED'}"
          f" · seeds={_SEEDS} · model={_MODEL} · tasks={len(SUITE)}"
          f" (halves {HALF}/{len(SUITE) - HALF}) · timeout={_TIMEOUT}s", flush=True)

    root = Path(tempfile.mkdtemp(prefix="chimlearn-ws-"))
    homes_root = Path(tempfile.mkdtemp(prefix="chimlearn-home-"))
    tampered: set[str] = set()
    accum: list[dict[str, object]] = []
    # Pooled paired pairs across all seeds: item = the SAME task in the SAME seed, both arms from an
    # identical fresh workspace. `pool_cold[i]`/`pool_learn[i]` are that pair — what compare_paired needs.
    pool_cold: list[bool] = []
    pool_learn: list[bool] = []
    per_seed: list[dict[str, object]] = []
    total_uses = 0
    skill_stats_last: list[dict[str, object]] = []
    try:
        for seed in range(1, _SEEDS + 1):
            if _SEEDS > 1:
                print(f"\n--- seed {seed}/{_SEEDS} ---", flush=True)
            homes = homes_root / f"seed{seed}"
            arms = {arm: _run_arm(arm, seed, root, homes, tampered, accum)
                    for arm in ("cold", "learning")}
            pool_cold.extend(arms["cold"])
            pool_learn.extend(arms["learning"])
            stats = _skill_stats(homes / "learning")
            skill_stats_last = stats
            total_uses += sum(int(s.get("uses", 0)) for s in stats)
            per_seed.append({
                "seed": seed,
                "cold": arms["cold"], "learning": arms["learning"],
                "did": _did(arms["cold"], arms["learning"]),
                "skills_learned": _skills_learned(homes / "learning"),
            })
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(homes_root, ignore_errors=True)

    from chimera.eval.paired import compare_paired  # McNemar + Wilson on discordant pairs

    paired = compare_paired(pool_cold, pool_learn, baseline_name="cold", treatment_name="learning")
    ci_lo, ci_hi = paired.diff_ci
    mean_did = sum(float(s["did"]) for s in per_seed) / len(per_seed)

    print("\n" + "=" * 66, flush=True)
    print(f"  POOLED PAIRED (n={paired.n} = {len(SUITE)} tasks x {_SEEDS} seed(s)) — the level-shift meter:",
          flush=True)
    print(f"    cold      {paired.baseline_rate:6.1%}", flush=True)
    print(f"    learning  {paired.treatment_rate:6.1%}", flush=True)
    print(f"    paired Δ  {paired.delta:+.1%}   95% CI [{ci_lo:+.1%}, {ci_hi:+.1%}]   "
          f"-> {'SIGNIFICANT (CI excludes 0)' if paired.significant else 'not significant (CI includes 0)'}",
          flush=True)
    print(f"    discordant pairs: learning +{paired.treatment_only} / cold +{paired.baseline_only}"
          f"  (concordant {paired.both_pass + paired.both_fail} carry no signal)", flush=True)
    print(f"\n  difference-in-differences (slope meter, per seed): "
          f"{[round(float(s['did']), 3) for s in per_seed]}  mean {mean_did:+.1%}", flush=True)
    print(f"  skills kept (per seed): {[s['skills_learned'] for s in per_seed]}", flush=True)
    print(f"  grading integrity: {'TAMPERED: ' + ', '.join(sorted(tampered)) if tampered else 'no arm modified its own test'}",
          flush=True)

    # learn->use connection check (P5): `uses` is credited only when a card actually reached a prompt
    # on a verified run, so total uses == 0 means the loop is STILL open. A non-zero total is the proof
    # the fix connected; the per-card rate then tells transfer (helped) from noise (retrieved, didn't).
    used = sorted((s for s in skill_stats_last if int(s.get("uses", 0)) > 0),
                  key=lambda s: -int(s.get("uses", 0)))
    end_bullets = int(accum[-1]["playbook_bullets"]) if accum else 0
    print(f"\n  learn->use connection ({'CONNECTED' if _CONNECT else 'DISCONNECTED'}): "
          f"skill-card retrievals credited = {total_uses} (all seeds); last-seed playbook bullets = {end_bullets}",
          flush=True)
    if _CONNECT and total_uses == 0 and end_bullets == 0:
        print("     !! STILL ZERO INJECTION — neither channel reached a prompt; the loop did not\n"
              "        connect (check flags/env). A null here is the disconnected null, not transfer.",
              flush=True)
    if used:
        print("  last-seed per-card attribution (name: uses/successes rate — rate<0.5 = retrieved but weak):",
              flush=True)
        for s in used[:12]:
            print(f"      {str(s.get('name','')):<28} {s.get('uses')}/{s.get('successes')}  rate={s.get('rate')}",
                  flush=True)

    if paired.baseline_rate >= 0.9 or paired.baseline_rate <= 0.1:
        print(f"\n  !! UNINFORMATIVE BY CONSTRUCTION — cold's pooled rate is {paired.baseline_rate:.1%}"
              " (ceiling/floor). Reported, not interpreted (PREREGISTRATION.md).", flush=True)
    if paired.n < 80:
        print(f"\n  note: n={paired.n} paired trials is small — a not-significant result is UNDERPOWERED,"
              " not 'no effect' (PREREGISTRATION.md). More seeds tighten the CI.", flush=True)

    (_OUT / "learning.json").write_text(
        json.dumps({
            "suite": SUITE_NAME, "model": _MODEL, "learn_use_connected": _CONNECT, "seeds": _SEEDS,
            "tasks": [str(t["id"]) for t in SUITE], "half": HALF,
            "paired": paired.summary(),
            "mean_did": mean_did, "per_seed": per_seed,
            "skill_card_uses": total_uses, "skill_stats_last_seed": skill_stats_last, "accumulation": accum,
            "graded_against_pristine_test": True, "tests_modified_by_solve": sorted(tampered),
        }, indent=2),
        encoding="utf-8",
    )
    print(f"\n  wrote {(_OUT / 'learning.json')}", flush=True)


if __name__ == "__main__":
    main()
