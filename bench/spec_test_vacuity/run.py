"""How many generated spec tests pass on the buggy code they were meant to catch?
Registered in `PREREGISTRATION.md` before any model call.

For each `bench/local_lift` task that ships a pre-existing workspace: the production requirement
extractor, the production spec-test generator on the buggy base, and `pytest -q -rA` on that base.
A test that passes there could not have detected the bug the task is about.

    python bench/spec_test_vacuity/run.py --out bench/spec_test_vacuity/results/<tag>.jsonl
    python bench/spec_test_vacuity/run.py --report bench/spec_test_vacuity/results/<tag>.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bench" / "local_lift"))

from tasks import TASKS  # noqa: E402  (bench/local_lift)

from chimera.config import get_settings  # noqa: E402
from chimera.core.checklist import RequirementChecklist  # noqa: E402
from chimera.core.spec_test import (  # noqa: E402
    _TEST_FILE,
    SpecTestGenerator,
    parse_outcomes,
    workspace_digest,
)
from chimera.eval.anytime import wilson_bounds  # noqa: E402
from chimera.orchestration.receipts import price_completion  # noqa: E402


def _materialise(files: dict[str, str], root: Path) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _pytest(root: Path, file: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", file],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


@dataclass
class Row:
    task_id: str
    hidden_test_fails_on_base: bool
    requirements: int
    generated_chars: int
    tests: int
    passed_on_base: int
    failed_on_base: int
    errored_on_base: int
    all_pass_on_base: bool
    outcomes: dict[str, str]
    usd: float | None
    seconds: float


class _Metered:
    def __init__(self, inner: Any) -> None:
        self.inner, self.usd, self.unpriced = inner, 0.0, False

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        result = self.inner.complete(messages, **kwargs)
        cost = price_completion(result)
        self.usd += cost.usd
        self.unpriced = self.unpriced or cost.unpriced is not None
        return result


def one(task: dict[str, Any], *, model: str) -> Row:
    from chimera.providers import LLMGateway

    t0 = time.monotonic()
    backend = _Metered(LLMGateway())
    with tempfile.TemporaryDirectory(prefix="vacuity-") as tmp:
        base = Path(tmp)
        _materialise(task["files"], base)
        # Instrument: the hidden test must FAIL on the base, or there is no bug to detect.
        (base / task["test"]).write_text(task["test_src"], encoding="utf-8")
        rc, _ = _pytest(base, task["test"])
        hidden_fails = rc != 0
        (base / task["test"]).unlink()
        requirements = RequirementChecklist(backend, model).extract(task["prompt"])
        code = SpecTestGenerator(backend, model).generate(
            task["prompt"], requirements, code_context=workspace_digest(base)
        )
        outcomes: dict[str, str] = {}
        if code:
            (base / _TEST_FILE).write_text(code, encoding="utf-8")
            _, out = _pytest(base, _TEST_FILE)
            outcomes = parse_outcomes(out, _TEST_FILE)
    passed = sum(v == "PASSED" for v in outcomes.values())
    failed = sum(v == "FAILED" for v in outcomes.values())
    errored = sum(v == "ERROR" for v in outcomes.values())
    return Row(
        task_id=task["id"], hidden_test_fails_on_base=hidden_fails, requirements=len(requirements),
        generated_chars=len(code), tests=len(outcomes), passed_on_base=passed, failed_on_base=failed,
        errored_on_base=errored, all_pass_on_base=bool(outcomes) and passed == len(outcomes),
        outcomes=outcomes, usd=(None if backend.unpriced else backend.usd),
        seconds=round(time.monotonic() - t0, 1),
    )


def report(path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    valid = [r for r in rows if r["hidden_test_fails_on_base"]]
    excluded = [r["task_id"] for r in rows if not r["hidden_test_fails_on_base"]]
    with_tests = [r for r in valid if r["tests"]]
    funcs = sum(r["tests"] for r in with_tests)
    vac = sum(r["passed_on_base"] for r in with_tests)
    lo, hi = wilson_bounds(vac, funcs) if funcs else (0.0, 1.0)
    all_pass = sum(r["all_pass_on_base"] for r in with_tests)
    lines = [
        f"# spec_test_vacuity — {len(rows)} tasks, US$ {sum(r['usd'] or 0 for r in rows):.4f}",
        "",
        f"- instrument: hidden test fails on the base in {len(valid)}/{len(rows)} tasks"
        + (f" (excluded: {', '.join(excluded)})" if excluded else ""),
        f"- tasks where the generator produced a module: {len(with_tests)}/{len(valid)}",
        (f"- generated test functions: {funcs}; **pass on the buggy base: {vac}/{funcs}** "
         f"({vac / funcs:.2f}, Wilson 95% [{lo:.2f}, {hi:.2f}])") if funcs else "- no functions",
        f"- fail on the base: {sum(r['failed_on_base'] for r in with_tests)}; error: {sum(r['errored_on_base'] for r in with_tests)}",
        f"- **tasks where EVERY generated test passes on the buggy base: {all_pass}/{len(with_tests)}** — "
        f"the shipped verifier would have reported a green `evidence=\"verifier\"` on a workspace that still holds the bug",
        "",
        "| task | reqs | tests | pass on base | fail | error | all pass |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in sorted(valid, key=lambda r: r["task_id"]):
        lines.append(f"| `{r['task_id']}` | {r['requirements']} | {r['tests']} | {r['passed_on_base']} | "
                     f"{r['failed_on_base']} | {r['errored_on_base']} | {'**yes**' if r['all_pass_on_base'] else ''} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    if args.report:
        print(report(Path(args.report)))
        return 0
    settings = get_settings()
    if not settings.has_any_key():
        print("no provider key in the environment", file=sys.stderr)
        return 2
    if settings.cache:
        print("CHIMERA_CACHE is on; aborting", file=sys.stderr)
        return 2
    model = args.model or settings.default_model
    tasks = [t for t in TASKS if t["files"]]
    if args.limit:
        tasks = tasks[: args.limit]
    out = Path(args.out) if args.out else Path(__file__).with_name("results") / f"{time.strftime('%Y-%m-%d')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["task_id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}
    todo = [t for t in tasks if t["id"] not in done]
    print(f"model {model}; {len(todo)} tasks to run, {len(done)} on disk")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    spent = 0.0
    with out.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, t, model=model): t["id"] for t in todo}
        for fut in as_completed(futures):
            tid = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001 — a provider hiccup must not lose the file
                print(f"  {tid:<24} ERROR {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                continue
            spent += r.usd or 0.0
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {tid:<24} hidden-fails={r.hidden_test_fails_on_base} tests={r.tests} pass-on-base={r.passed_on_base} "
                  f"fail={r.failed_on_base} err={r.errored_on_base} {r.seconds}s  Σ US$ {spent:.4f}", flush=True)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
