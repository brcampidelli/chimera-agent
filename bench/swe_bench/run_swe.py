"""SWE-bench runner — produce predictions for the paired A/B, honestly.

Executes the design fixed in PREREGISTRATION.md (+ Amendment 1). Read that first: it fixes the slice
(django `<15 min fix`), the model (deepseek-chat-v3.1), the arms, the no-verify policy, and Q1-only.

What this does (the glue the harness does NOT provide):
  1. Clone django ONCE as a local reference.
  2. Per instance: make a fresh checkout at base_commit, SANITIZED with the harness's own anti-leakage
     recipe (single-branch, reset --hard, remote removed, future work gc'd unreachable) so the agent
     cannot reach the fix through git history.
  3. Run `chimera solve` for one arm on that checkout, `git diff` the result → the model_patch.
  4. Write predictions in the harness's schema (instance_id, model_name_or_path, model_patch).

What this does NOT do: grade. Grading is the official `swebench` harness in Docker, run separately, so
the pass/fail verdict is never ours. This file only produces patches.

Usage (WSL, swebench venv NOT needed here — this drives the chimera CLI):
  BENCH_ARM=baseline|treatment  BENCH_SLICE=results/slice.jsonl  \
    uv run --extra dev python bench/swe_bench/run_swe.py
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/deepseek/deepseek-chat-v3.1")
_ARM = os.environ.get("BENCH_ARM", "treatment")
_SLICE = Path(os.environ.get("BENCH_SLICE", str(Path(__file__).resolve().parent / "results" / "slice.jsonl")))
_OUT = Path(os.environ.get("BENCH_OUT", str(Path(__file__).resolve().parent / "results")))
_REF = Path(os.environ.get("SWE_DJANGO_REF", "/tmp/swe-django-ref"))
_WORK = Path(os.environ.get("SWE_WORK", "/tmp/swe-work"))
_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "900"))
_REPO_URL = "https://github.com/django/django"

# Arm flags, fixed by the pre-registration. Both arms share the no-learning hygiene; the ONLY
# difference is the scaffolding. No --verify (Amendment 1, decision 3: no executable ground truth,
# because the only tests are the hidden graders and handing them over would be leakage).
# --keep-workspace on BOTH arms: SWE-bench's own tests decide pass/fail, so the agent's final edits
# must stay on disk. Without it, Chimera's verify-or-revert rolls the tree back to base on a failed
# solve and `git diff` yields an empty patch — which is what a first probe showed (all-empty), a
# grading-owner confusion, not a floor. Identical on both arms, so it cannot bias the comparison.
_HYGIENE = ["--no-remember", "--no-collect", "--no-evolve-skills", "--keep-workspace"]
# Amendment 2: --max-steps is a RESOURCE budget, not scaffolding, so it is identical on every arm.
# Giving the extra steps only to the treatment would confound "Chimera helps" with "more steps help".
# Run 1 used the default (8) against a 250 MB repo — recorded there as our misconfiguration.
_STEPS = ["--max-steps", os.environ.get("BENCH_MAX_STEPS", "30")]
_SCAFFOLD = ["--repo-map", "--progress-ledger", "--replan", "--checklist", "--max-attempts", "3"]
_ARMS: dict[str, list[str]] = {
    "baseline": ["--no-plan", "--no-manager", "--max-attempts", "1", *_STEPS, *_HYGIENE],
    "treatment": [*_SCAFFOLD, *_STEPS, *_HYGIENE],
    # The discriminating arm: the diff-gate promoted from observer to gate.
    "treatment_diff": [*_SCAFFOLD, "--require-diff", *_STEPS, *_HYGIENE],
}
_INSTRUCTION = (
    "You are working in the checked-out django repository at a specific commit. Resolve this issue by "
    "editing the code so the project's tests pass. Do NOT edit any test files.\n\nIssue:\n{problem}"
)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                          errors="replace", timeout=timeout, check=False)


def _ensure_reference() -> None:
    """Clone django once; reused for every instance's isolated checkout."""
    if (_REF / ".git").exists():
        return
    print(f"cloning django reference -> {_REF} (once, ~250MB) ...", flush=True)
    _REF.parent.mkdir(parents=True, exist_ok=True)
    r = _run(["git", "clone", _REPO_URL, str(_REF)], timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"reference clone failed: {r.stderr[-500:]}")


def _prepare_workspace(instance: dict) -> Path:
    """A fresh checkout at base_commit, sanitized so the fix is unreachable via git.

    The harness's own recipe: clone from the local reference, hard-reset to base_commit, drop the
    remote, then expire reflog + gc --prune so every commit after base_commit is physically gone.
    """
    ws = _WORK / f"{_ARM}__{instance['instance_id']}"
    if ws.exists():
        _run(["rm", "-rf", str(ws)])
    ws.parent.mkdir(parents=True, exist_ok=True)
    # --no-tags matters: a --single-branch clone still brings ALL tags, and django's stable/*.x tags
    # reach every future commit — including the fix. Without this the agent could `git log stable/5.0.x`
    # and read the answer. Clone without tags, reset the branch back, drop the remote, delete any tag
    # that slipped in, then gc so the now-unreachable future objects are physically gone.
    for cmd in (
        ["git", "clone", "--no-hardlinks", "--single-branch", "--no-tags", str(_REF), str(ws)],
        ["git", "-C", str(ws), "reset", "--hard", instance["base_commit"]],
        ["git", "-C", str(ws), "remote", "remove", "origin"],
    ):
        r = _run(cmd, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"workspace step {cmd[1:4]} failed: {r.stderr[-300:]}")
    tags = _run(["git", "-C", str(ws), "tag"]).stdout.split()
    if tags:
        _run(["git", "-C", str(ws), "tag", "-d", *tags])
    _run(["git", "-C", str(ws), "reflog", "expire", "--expire=now", "--all"])
    _run(["git", "-C", str(ws), "gc", "--prune=now"], timeout=600)
    # Anti-leakage assertion: NO ref anywhere may reach a commit that isn't an ancestor of base_commit.
    # (`rev-list --all ^base` is the correct check; `log --all base..HEAD` is not — --all overrides the range.)
    n_future = _run(["git", "-C", str(ws), "rev-list", "--all", "--count", f"^{instance['base_commit']}"]).stdout.strip()
    if n_future not in ("", "0"):
        raise RuntimeError(f"SANITIZE FAILED: {n_future} commits after base_commit still reachable in {ws}")
    return ws


def _solve(instance: dict, ws: Path) -> dict:
    """Run one arm's chimera solve, return the prediction row + telemetry."""
    task = _INSTRUCTION.format(problem=instance["problem_statement"])
    argv = ["chimera", "solve", task, "--workspace", str(ws), "--model", _MODEL, "--stream", *_ARMS[_ARM]]
    started = time.monotonic()
    timed_out = False
    try:
        _run(argv, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
    elapsed = time.monotonic() - started
    # The patch is whatever the working tree changed vs base_commit — the machine truth.
    diff = _run(["git", "-C", str(ws), "diff", instance["base_commit"]])
    patch = diff.stdout if diff.returncode == 0 else ""
    return {
        "instance_id": instance["instance_id"],
        "model_name_or_path": f"chimera-{_ARM}",
        "model_patch": patch,
        "_seconds": round(elapsed, 1),
        "_timed_out": timed_out,
        "_empty": not patch.strip(),
    }


def main() -> None:
    if _ARM not in _ARMS:
        raise SystemExit(f"BENCH_ARM must be one of {list(_ARMS)}, got {_ARM!r}")
    instances = [json.loads(line) for line in _SLICE.read_text(encoding="utf-8").splitlines() if line.strip()]
    _OUT.mkdir(parents=True, exist_ok=True)
    _ensure_reference()
    print(f"SWE-bench runner | arm={_ARM} | model={_MODEL} | {len(instances)} instances | timeout={_TIMEOUT}s",
          flush=True)

    preds_path = _OUT / f"predictions_{_ARM}.jsonl"
    telemetry: list[dict] = []
    with preds_path.open("w", encoding="utf-8") as fh:
        for i, inst in enumerate(instances, start=1):
            try:
                ws = _prepare_workspace(inst)
                row = _solve(inst, ws)
            except (RuntimeError, subprocess.SubprocessError) as exc:
                # Infra failure is recorded, never scored as a model failure (prereg reporting rule 3).
                row = {"instance_id": inst["instance_id"], "model_name_or_path": f"chimera-{_ARM}",
                       "model_patch": "", "_seconds": 0.0, "_timed_out": False, "_empty": True,
                       "_infra_error": str(exc)[:200]}
            fh.write(json.dumps({k: v for k, v in row.items() if not k.startswith("_")}) + "\n")
            fh.flush()
            telemetry.append(row)
            flag = "INFRA" if row.get("_infra_error") else ("TIMEOUT" if row["_timed_out"] else
                   ("EMPTY" if row["_empty"] else "patch"))
            print(f"  [{i:>2}/{len(instances)}] {inst['instance_id']:<28} {flag:<7} {row['_seconds']:.0f}s",
                  flush=True)
            _run(["rm", "-rf", str(_WORK / f'{_ARM}__{inst["instance_id"]}')])  # reclaim disk

    (_OUT / f"telemetry_{_ARM}.json").write_text(json.dumps(telemetry, indent=2), encoding="utf-8")
    empties = sum(1 for t in telemetry if t["_empty"])
    infra = sum(1 for t in telemetry if t.get("_infra_error"))
    print(f"\nwrote {preds_path}  ({len(telemetry)} preds, {empties} empty, {infra} infra-errors)", flush=True)


if __name__ == "__main__":
    main()
