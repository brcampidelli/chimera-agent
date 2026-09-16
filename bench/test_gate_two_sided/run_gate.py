"""Steps 2, 3 and S2 of `PREREGISTRATION.md`.

    python bench/test_gate_two_sided/run_gate.py --generate            # step 2: one module per task, stored
    python bench/test_gate_two_sided/run_gate.py --read                # step 3: FDR, FTR, false alarms, gate accuracy
    python bench/test_gate_two_sided/run_gate.py --s2                  # the diff rule on the accepted-patch history

Everything after `--generate` is deterministic and spends nothing: pytest on materialised trees,
`coverage` on the buggy base for the fault-trigger side, and the rule of
`chimera/governance/diff_rules.py` over full file texts. Halted or unpatched rows leave every
denominator (PROTOCOL §2) and are counted where they fell out.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bench" / "local_lift"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from tasks import TASKS  # noqa: E402  (bench/local_lift)

from chimera.core.checklist import RequirementChecklist  # noqa: E402
from chimera.core.spec_test import (  # noqa: E402
    SpecTestGenerator,
    _strip_fence,
    parse_outcomes,
    workspace_digest,
)
from chimera.eval.anytime import wilson_bounds  # noqa: E402
from chimera.governance.diff_rules import flag_snapshots  # noqa: E402
from chimera.orchestration.receipts import price_completion  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
TEST_FILE = "test_spec_generated.py"
PY = sys.executable


def fix_tasks() -> dict[str, dict[str, Any]]:
    return {str(t["id"]): t for t in TASKS if str(t["id"]).startswith("fix_")}


def materialise(files: dict[str, str | None], root: Path) -> None:
    for rel, content in files.items():
        target = root / rel
        if content is None:
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def pytest_outcomes(root: Path, test_file: str, extra: list[str] | None = None) -> tuple[int, dict[str, str], str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", *(extra or []), test_file],
        cwd=str(root), capture_output=True, text=True, errors="replace", check=False, timeout=180,
    )
    out = proc.stdout or ""
    return proc.returncode, parse_outcomes(out, test_file), out


class _Metered:
    def __init__(self, inner: Any) -> None:
        self.inner, self.usd, self.unpriced = inner, 0.0, False

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        result = self.inner.complete(messages, **kwargs)
        cost = price_completion(result)
        self.usd += cost.usd
        self.unpriced = self.unpriced or cost.unpriced is not None
        return result


# ---------------------------------------------------------------------------------------------
# Step 2 — generation, stored
# ---------------------------------------------------------------------------------------------

def generate(model: str, out: Path) -> None:
    from chimera.providers import LLMGateway

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        for task_id, task in fix_tasks().items():
            t0 = time.monotonic()
            backend = _Metered(LLMGateway())
            with tempfile.TemporaryDirectory(prefix="testgate-gen-") as tmp:
                base = Path(tmp)
                materialise(task["files"], base)
                requirements = RequirementChecklist(backend, model).extract(task["prompt"])
                module = SpecTestGenerator(backend, model).generate(
                    task["prompt"], requirements, code_context=workspace_digest(base)
                )
            row = {
                "task_id": task_id, "requirements": [getattr(r, "text", str(r)) for r in requirements],
                "module": module, "usd": (None if backend.unpriced else backend.usd),
                "seconds": round(time.monotonic() - t0, 1),
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"  {task_id:<22} reqs={len(requirements)} module={'yes' if module else 'NO'} "
                  f"chars={len(module)} usd={row['usd']}", flush=True)


# ---------------------------------------------------------------------------------------------
# Step 3 — readings
# ---------------------------------------------------------------------------------------------

def _changed_base_lines(before: str, after: str) -> set[int]:
    """Base-file line numbers a patch removed or changed (the fault region's contribution).

    An insert-only fix — a missing guard added between two existing lines — removes and changes
    nothing, so the first version of this left three tasks with an empty region and no FTR at all.
    The fault of an insert-only fix is the place where the insert went: the base lines on either
    side of it are the region, which is where a test that reaches the bug executes.
    """
    out: set[int] = set()
    n = len(before.splitlines())
    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines())
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            out.update(range(i1 + 1, i2 + 1))
        elif tag == "insert":
            out.update(ln for ln in (i1, i1 + 1) if 1 <= ln <= n)
    return out


def _covered_lines(root: Path, test_file: str, test_name: str, verify_rel: str) -> set[int] | None:
    """Lines of ``verify_rel`` executed when ONE test function runs on this tree, via coverage."""
    data = root / ".coverage.testgate"
    with contextlib.suppress(FileNotFoundError):
        data.unlink()
    env = dict(os.environ, COVERAGE_FILE=str(data))
    subprocess.run(
        [PY, "-m", "coverage", "run", "--include", verify_rel, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         f"{test_file}::{test_name}"],
        cwd=str(root), capture_output=True, text=True, errors="replace", check=False, timeout=180, env=env,
    )
    if not data.exists():
        return None
    proc = subprocess.run(
        [PY, "-m", "coverage", "json", "-o", "-", "--include", verify_rel],
        cwd=str(root), capture_output=True, text=True, errors="replace", check=False, timeout=60, env=env,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    for path, info in report.get("files", {}).items():
        if Path(path).as_posix().endswith(verify_rel):
            return set(info.get("executed_lines", []))
    return set()


def read(patches_path: Path, generated_path: Path, out: Path, *, raw_modules: bool = False) -> None:
    tasks = fix_tasks()
    patches = [json.loads(ln) for ln in patches_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    generated = {r["task_id"]: r for r in (json.loads(ln) for ln in generated_path.read_text(encoding="utf-8").splitlines() if ln.strip())}

    per_test: list[dict[str, Any]] = []
    per_patch: list[dict[str, Any]] = []
    tasks_without_module: list[str] = []
    for task_id, task in tasks.items():
        gen = generated.get(task_id)
        module = (gen or {}).get("module") or ""
        # The stored module is what the generator returned on the day; the shipped stripper is
        # applied again so a reading reflects the code as it is now (`--raw-modules` keeps the
        # day's bytes, for the before/after in RESULTS.md).
        if not raw_modules:
            module = _strip_fence(module)
        if not module.strip():
            tasks_without_module.append(task_id)
            continue
        verify_rel = task["verify"]
        task_patches = [p for p in patches if p["task_id"] == task_id and p["label"] != "unpatched"]
        correct = [p for p in task_patches if p["label"] == "correct"]
        # The fault region: base lines any CORRECT patch removed or changed.
        fault: set[int] = set()
        for p in correct:
            after = p["files"].get(verify_rel)
            if isinstance(after, str):
                fault |= _changed_base_lines(task["files"][verify_rel], after)

        with tempfile.TemporaryDirectory(prefix="testgate-read-") as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            materialise(task["files"], base)
            (base / TEST_FILE).write_text(module, encoding="utf-8")
            _, on_base, base_out = pytest_outcomes(base, TEST_FILE)
            # Hidden test must fail on the base — the instrument, as the vacuity bench checked.
            (base / task["test"]).write_text(task["test_src"], encoding="utf-8")
            hidden_rc, _, _ = pytest_outcomes(base, task["test"])
            (base / task["test"]).unlink()
            coverage_by_test: dict[str, set[int] | None] = {}
            if fault:
                for name in on_base:
                    coverage_by_test[name] = _covered_lines(base, TEST_FILE, name, verify_rel)

            # Each patch tree: outcomes of the same module.
            outcomes_by_patch: dict[str, dict[str, str]] = {}
            for p in task_patches:
                key = f"{p['model'].split('/')[-1]}-r{p['replica']}"  # two arms share replica numbers
                tree = Path(tmp) / f"patch-{key}"
                tree.mkdir()
                materialise(task["files"], tree)
                materialise(p["files"], tree)
                (tree / TEST_FILE).write_text(module, encoding="utf-8")
                rc, on_patch, _ = pytest_outcomes(tree, TEST_FILE)
                outcomes_by_patch[key] = on_patch
                # Gate verdicts, module level.
                if not on_patch:
                    bilateral = "FAIL"  # collection error / crash: the candidate failed, as shipped
                else:
                    vacuous = [t for t, s in on_patch.items() if s == "PASSED" and on_base.get(t) == "PASSED"]
                    discriminating = [t for t, s in on_patch.items() if s == "PASSED" and on_base.get(t) != "PASSED"]
                    bad = [t for t, s in on_patch.items() if s != "PASSED"]
                    if bad:
                        bilateral = "FAIL"
                    elif not discriminating:
                        bilateral = "ABSTAIN"
                    else:
                        bilateral = "PASS"
                    _ = vacuous
                exit_code = "PASS" if rc == 0 else "FAIL"
                per_patch.append({
                    "task_id": task_id, "replica": p["replica"], "model": p["model"], "label": p["label"],
                    "bilateral": bilateral, "exit_code": exit_code,
                    "failing_tests": sorted(t for t, s in on_patch.items() if s != "PASSED"),
                })

            for name, status in on_base.items():
                fails_on_base = status != "PASSED"
                cov = coverage_by_test.get(name)
                reaches = (bool(cov & fault) if cov is not None else None) if fault else None
                fa_pairs = [
                    outcomes_by_patch[f"{p['model'].split('/')[-1]}-r{p['replica']}"].get(name) for p in correct
                ]
                false_alarms = sum(1 for s in fa_pairs if s is not None and s != "PASSED")
                per_test.append({
                    "task_id": task_id, "test": name, "fails_on_base": fails_on_base,
                    "reaches_fault": reaches, "fault_lines": sorted(fault),
                    "correct_patches": len(correct), "false_alarms": false_alarms,
                })
        print(f"  {task_id:<22} tests={len(on_base):<2} hidden_fails_on_base={hidden_rc != 0} "
              f"patches={len(task_patches)} correct={len(correct)} fault_lines={len(fault)}", flush=True)

    out.write_text(json.dumps({"per_test": per_test, "per_patch": per_patch,
                               "tasks_without_module": tasks_without_module}, indent=1), encoding="utf-8")
    report_readings(per_test, per_patch, tasks_without_module)


def report_readings(per_test: list[dict[str, Any]], per_patch: list[dict[str, Any]], no_module: list[str]) -> None:
    n = len(per_test)
    fdr = sum(t["fails_on_base"] for t in per_test)
    with_ftr = [t for t in per_test if t["reaches_fault"] is not None]
    ftr = sum(t["reaches_fault"] for t in with_ftr)
    reach_not_detect = sum(1 for t in with_ftr if t["reaches_fault"] and not t["fails_on_base"])
    pairs = sum(t["correct_patches"] for t in per_test)
    fa = sum(t["false_alarms"] for t in per_test)
    fa_tests = sum(1 for t in per_test if t["false_alarms"])
    with_correct = sum(1 for t in per_test if t["correct_patches"])
    print(f"\n== B4 — per generated test function (n={n}; tasks without a module: {len(no_module)}) ==")
    lo, hi = wilson_bounds(fdr, n) if n else (0, 0)
    print(f"  FDR  fails on the buggy base:            {fdr}/{n} = {fdr / n:.3f}  Wilson [{lo:.2f}, {hi:.2f}]")
    if with_ftr:
        lo, hi = wilson_bounds(ftr, len(with_ftr))
        print(f"  FTR  reaches the fault region:           {ftr}/{len(with_ftr)} = {ftr / len(with_ftr):.3f}  Wilson [{lo:.2f}, {hi:.2f}]"
              f"   (tests on tasks with ≥1 correct patch)")
        print(f"       reaches the fault and does NOT fail: {reach_not_detect}/{len(with_ftr)}")
    if pairs:
        lo, hi = wilson_bounds(fa, pairs)
        print(f"  FALSE ALARM  fails a correct patch:      {fa}/{pairs} (test, correct patch) pairs = {fa / pairs:.3f}  Wilson [{lo:.2f}, {hi:.2f}]")
        print(f"               tests false-alarming on ≥1 correct patch: {fa_tests}/{with_correct}")

    labels = Counter(p["label"] for p in per_patch)
    print(f"\n== B3 — gate accuracy over labelled patches: {dict(labels)} ==")
    for verdict_key in ("bilateral", "exit_code"):
        by = defaultdict(Counter)
        for p in per_patch:
            by[p["label"]][p[verdict_key]] += 1
        correct_n = sum(by["correct"].values())
        incorrect_n = sum(by["incorrect"].values())
        tpr = by["correct"]["PASS"] / correct_n if correct_n else float("nan")
        tnr = by["incorrect"]["FAIL"] / incorrect_n if incorrect_n else float("nan")
        reverted = by["correct"]["FAIL"] / correct_n if correct_n else float("nan")
        print(f"  {verdict_key:10} TPR P(PASS|correct)={tpr:.3f}  TNR P(FAIL|incorrect)={tnr:.3f}  "
              f"correct→FAIL (reverted good work)={reverted:.3f}  "
              f"correct={dict(by['correct'])}  incorrect={dict(by['incorrect'])}")


# ---------------------------------------------------------------------------------------------
# S2 — the diff rule on the accepted-patch history
# ---------------------------------------------------------------------------------------------

def _py_files(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if "__pycache__" in rel or "/.chimera/" in f"/{rel}" or rel.startswith(".chimera"):
            continue
        with contextlib.suppress(OSError, UnicodeDecodeError):
            out[rel] = path.read_text(encoding="utf-8")
    return out


def s2(patches_path: Path, harness_home: Path, out: Path) -> None:
    rows: list[dict[str, Any]] = []
    # (a) the factorial's kept workspaces, diffed against their task fixtures.
    results = harness_home / "data_try6" / "results"
    fixtures_cache: dict[str, dict[str, str]] = {}
    scanned = missing = 0
    for result_json in sorted(results.rglob("*.json")):
        try:
            d = json.loads(result_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        task = d.get("task_id")
        ws = Path(str(d.get("workspace") or ""))
        score = (d.get("oracle_result") or {}).get("outcome_score")
        if not task or not ws.is_dir() or not isinstance(score, (int, float)):
            missing += 1
            continue
        if task not in fixtures_cache:
            fixtures_cache[task] = _py_files(harness_home / "tasks" / task / "fixtures")
        flags = flag_snapshots(fixtures_cache[task], _py_files(ws))
        scanned += 1
        rows.append({"source": "factorial", "task": task, "arm": d.get("model_id"), "score": score,
                     "accepted": score >= 0.8, "flags": [f.render() for f in flags]})
    # (b) the correct local_lift patches of step 1.
    if patches_path.is_file():
        tasks = fix_tasks()
        for p in (json.loads(ln) for ln in patches_path.read_text(encoding="utf-8").splitlines() if ln.strip()):
            if p["label"] == "unpatched":
                continue
            before = {k: v for k, v in tasks[p["task_id"]]["files"].items() if k.endswith(".py")}
            after = dict(before)
            for rel, content in p["files"].items():
                if content is None:
                    after.pop(rel, None)
                else:
                    after[rel] = content
            flags = flag_snapshots(before, after)
            rows.append({"source": "local_lift", "task": p["task_id"], "arm": f"r{p['replica']}",
                         "score": None, "accepted": p["label"] == "correct", "flags": [f.render() for f in flags]})
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\n== S2 — the diff rule on the patch history (factorial: {scanned} workspaces read, {missing} rows without one) ==")
    for source in ("factorial", "local_lift"):
        sub = [r for r in rows if r["source"] == source]
        for accepted in (True, False):
            group = [r for r in sub if r["accepted"] == accepted]
            if not group:
                continue
            flagged = sum(1 for r in group if r["flags"])
            lo, hi = wilson_bounds(flagged, len(group))
            label = "accepted (oracle ≥ 0.8)" if accepted else "not accepted"
            if source == "local_lift":
                label = "correct" if accepted else "incorrect"
            print(f"  {source:10} {label:26} flagged {flagged}/{len(group)} = {flagged / len(group):.3f}  Wilson [{lo:.2f}, {hi:.2f}]")
    flagged_rows = [r for r in rows if r["flags"]]
    sinks = Counter(f.split(": ", 1)[1].split("(")[0] for r in flagged_rows for f in r["flags"])
    print(f"  sinks behind the flags: {dict(sinks)}")
    print("  every flagged patch:")
    for r in flagged_rows:
        for f in r["flags"]:
            print(f"    [{r['source']}/{r['task']}/{r['arm']} score={r['score']}] {f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--read", action="store_true")
    ap.add_argument("--s2", action="store_true")
    ap.add_argument("--model", default="openrouter/deepseek/deepseek-v4-flash-0731")
    ap.add_argument("--patches", type=Path, default=RESULTS / "patches.jsonl")
    ap.add_argument("--generated", type=Path, default=RESULTS / "generated.jsonl")
    ap.add_argument("--harness-home", type=Path, default=Path(os.path.expanduser("~/harness-bench")))
    ap.add_argument("--raw-modules", action="store_true", help="read the modules exactly as stored, without re-stripping")
    args = ap.parse_args()
    if args.generate:
        generate(args.model, args.generated)
    if args.read:
        read(args.patches, args.generated, RESULTS / ("readings-raw.json" if args.raw_modules else "readings.json"),
             raw_modules=args.raw_modules)
    if args.s2:
        s2(args.patches, args.harness_home, RESULTS / "s2.json")
    if not (args.generate or args.read or args.s2):
        ap.error("pass --generate, --read and/or --s2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
