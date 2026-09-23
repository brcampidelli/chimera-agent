"""The Manager shown the on-disk diff — see PREREGISTRATION.md (written first).

    python -m bench.manager_diff.run --build     # evidence per row, US$ 0 (reads the WSL sandboxes)
    python -m bench.manager_diff.run --arm       # the production Manager with the evidence, capped US$ 3
    python -m bench.manager_diff.run --report

Reads the sandboxes through the Windows path of the WSL filesystem, read-only.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.manager_p.run import MANAGER_MODEL, PASS, _rate  # noqa: E402
from bench.manager_p.run import corpus as claimed  # noqa: E402

HERE = Path(__file__).resolve().parent
SANDBOX = Path("//wsl.localhost/Ubuntu/home/brcamp/harness-bench/data_try6/sandbox")
EVIDENCE = HERE / "results" / "evidence.jsonl"
OUT = HERE / "results" / "manager_diff.jsonl"
USD_CAP = 3.0
DRAWS, SEED = 2000, 7


def workspace_for(task: str, arm: str, rep: int) -> Path | None:
    base = SANDBOX / f"arm-{arm}-r{rep}" / f"arm-{arm}-r{rep}"
    runs = sorted(base.glob(f"oc-bench-v2-{task}-arm-{arm}-r{rep}-*"))
    return (runs[-1] / "workspace") if runs else None


def evidence(workspace: Path) -> tuple[str, dict[str, Any]]:
    """The production wrapper (`autonomous.py`), from `before` = in/ + empty out/, `after` = as found."""
    from chimera.core.autonomous import _JUDGE_DIFF_CHARS
    from chimera.core.checkpoint import FileSnapshot, WorkspaceGuard
    from chimera.evolution.diff_gate import diff_snapshots, unified_diffs

    after = WorkspaceGuard(workspace).snapshot()
    before = FileSnapshot()
    for rel, text in after.files.items():
        if rel.startswith("in/"):
            before.files[rel] = text
    before.present = {rel for rel in after.present if rel.startswith("in/")}
    pdiff = diff_snapshots(before, after)
    diff_summary = pdiff.audit_summary()
    diffs = unified_diffs(before, after)
    if not diff_summary:
        return "", {"files": 0}
    bodies: list[str] = []
    budget = _JUDGE_DIFF_CHARS
    for file_diff in diffs:
        if budget <= 0:
            break
        body = file_diff.patch[:budget]
        budget -= len(body)
        bodies.append("--- " + file_diff.path + "\n" + body)
    ctx = "<<what-this-attempt-changed-on-disk>>\n" + diff_summary + "\n" + "\n".join(bodies) + "\n<<end>>"
    return ctx, {"files": len(diffs), "productive": bool(pdiff.is_productive), "chars": len(ctx)}


def build() -> None:
    rows = claimed()
    out: list[dict[str, Any]] = []
    missing = 0
    for row in rows:
        ws = workspace_for(row["task_id"], row["arm"], int(row["replica"]))
        if ws is None or not ws.exists():
            missing += 1
            continue
        ctx, meta = evidence(ws)
        out.append({"id": row["id"], "context": ctx, **meta})
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    print(json.dumps({"claimed": len(rows), "with_evidence": len(out), "sandbox_missing": missing,
                      "empty_diff": sum(1 for x in out if not x["context"])}, indent=2))


def run_arm() -> None:
    from chimera.core.supervisor import Manager
    from chimera.orchestration.receipts import price_completion
    from chimera.providers import LLMGateway

    ev = {json.loads(x)["id"]: json.loads(x) for x in EVIDENCE.read_text(encoding="utf-8").splitlines() if x.strip()}
    rows = {r["id"]: r for r in claimed()}
    seen = set()
    spent = 0.0
    if OUT.exists():
        for x in OUT.read_text(encoding="utf-8").splitlines():
            if x.strip():
                d = json.loads(x)
                seen.add(d["id"])
                spent += float(d.get("usd") or 0.0)

    class Metered:
        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.last = 0.0

        def complete(self, messages: Any, **kwargs: Any) -> Any:
            result = self.inner.complete(messages, **kwargs)
            cost = price_completion(result)
            if cost.unpriced:
                raise SystemExit(f"no price for {cost.unpriced} — refusing to meter it as US$ 0")
            self.last = float(cost.usd)
            return result

    backend = Metered(LLMGateway())
    manager = Manager(backend, MANAGER_MODEL)
    with OUT.open("a", encoding="utf-8") as fh:
        for rid, e in ev.items():
            if rid in seen:
                continue
            if spent >= USD_CAP:
                raise SystemExit(f"spend cap reached: US$ {spent:.3f}")
            row = rows[rid]
            t0 = time.perf_counter()
            backend.last = 0.0
            try:
                review = manager.review(row["task"], row["answer"], context=e["context"])
                verdict = "abstained" if review.abstained else ("approved" if review.approved else "revise")
                feedback, error = review.feedback[:300], ""
            except Exception as exc:  # noqa: BLE001 — recorded, never guessed
                verdict, feedback, error = "error", "", f"{type(exc).__name__}: {str(exc)[:200]}"
            spent += backend.last
            fh.write(json.dumps({"id": rid, "verdict": verdict, "feedback": feedback, "usd": backend.last,
                                 "error": error, "seconds": round(time.perf_counter() - t0, 2)}) + "\n")
            fh.flush()
    print(f"arm done, US$ {spent:.4f}")


def _disc(rows: list[dict[str, Any]]) -> float | None:
    pos = [r for r in rows if r["y"] == 1]
    neg = [r for r in rows if r["y"] == 0]
    if not pos or not neg:
        return None
    return sum(r["ok"] for r in pos) / len(pos) - sum(r["ok"] for r in neg) / len(neg)


def report() -> dict[str, Any]:
    base = {r["id"]: r for r in claimed()}
    rows = []
    for x in OUT.read_text(encoding="utf-8").splitlines():
        if x.strip():
            d = json.loads(x)
            if d["verdict"] in ("approved", "revise"):
                b = base[d["id"]]
                rows.append({"task": b["task_id"], "y": 1 if b["outcome"] >= PASS else 0, "ok": d["verdict"] == "approved"})
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r)
    tasks = sorted(by_task)
    rng = random.Random(SEED)
    draws = []
    for _ in range(DRAWS):
        sample = [r for _ in tasks for r in by_task[tasks[rng.randrange(len(tasks))]]]
        v = _disc(sample)
        if v is not None:
            draws.append(v)
    draws.sort()
    both = [t for t in tasks if len({r["y"] for r in by_task[t]}) == 2]
    within = [r for t in both for r in by_task[t]]
    all_rows = [json.loads(x) for x in OUT.read_text(encoding="utf-8").splitlines() if x.strip()]
    pos = [r for r in rows if r["y"] == 1]
    neg = [r for r in rows if r["y"] == 0]
    return {
        "rows": len(all_rows), "counted": len(rows),
        "other": {v: sum(1 for r in all_rows if r["verdict"] == v) for v in ("abstained", "error")},
        "TPR": _rate(sum(r["ok"] for r in pos), len(pos)), "FPR": _rate(sum(r["ok"] for r in neg), len(neg)),
        "discrimination": [_disc(rows), draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]],
        "within_10_tasks": {"tasks": len(both), "discrimination": _disc(within)},
        "usd": round(sum(float(r.get("usd") or 0) for r in all_rows), 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--arm", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.build:
        build()
    if args.arm:
        run_arm()
    if args.report:
        summary = report()
        (HERE / "results" / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
