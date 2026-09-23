"""The Manager's P(approved) against the oracle — see PREREGISTRATION.md (written first).

    python -m bench.manager_p.run --arm local      # qwen3:4b Noul, US$ 0
    python -m bench.manager_p.run --arm manager    # the production Manager, hosted, capped at US$ 2
    python -m bench.manager_p.run --report

Each arm writes ``results/<arm>.jsonl`` line by line and resumes from it.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.jev_decisions.report import auroc  # noqa: E402
from chimera.decisions import Noul, as_choice  # noqa: E402
from chimera.decisions.lint import errors  # noqa: E402
from chimera.decisions.local import LocalLogprobBackend  # noqa: E402

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "results" / "corpus.jsonl"
PASS = 0.8
NUM_CTX = 8192
MANAGER_MODEL = "openrouter/deepseek/deepseek-v3.2"
USD_CAP = 2.0
DRAWS, SEED = 2000, 7

ACCOMPLISHED = Noul(
    "accomplished",
    "You are a strict reviewer. Given a task and a worker's proposed result, judge the result. "
    "Does the worker's result accomplish the task?",
    criteria={
        "true": "the result correctly and completely accomplishes the task",
        "false": "the result is wrong, incomplete, or only claims to have done the work",
    },
)


def corpus() -> list[dict[str, Any]]:
    return [json.loads(x) for x in CORPUS.read_text(encoding="utf-8").splitlines() if x.strip()]


def message(row: dict[str, Any]) -> str:
    """The Manager's own user message (`supervisor.Manager.review`, no context)."""
    return f"Task:\n{row['task']}\n\nWorker's result:\n{row['answer']}"


def done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(x)["id"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()}


def run_local() -> None:
    if errors(ACCOMPLISHED):
        raise SystemExit(f"the question does not lint clean: {errors(ACCOMPLISHED)}")
    out = HERE / "results" / "local.jsonl"
    seen = done_ids(out)
    backend = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b")
    question = as_choice(ACCOMPLISHED)
    client = httpx.Client(timeout=300.0)
    with out.open("a", encoding="utf-8") as fh:
        for row in corpus():
            if row["id"] in seen:
                continue
            body = backend.body(message(row), question)
            body["options"]["num_ctx"] = NUM_CTX
            t0 = time.perf_counter()
            response = client.post("http://127.0.0.1:11434/api/chat", json=body)
            response.raise_for_status()
            data = response.json()
            tokens = int(data.get("prompt_eval_count") or 0)
            if not 0 < tokens < NUM_CTX:
                raise SystemExit(f"{row['id']}: prompt_eval_count {tokens} — truncated or unread")
            reading = backend.read(data, question)
            fh.write(json.dumps({"id": row["id"], "p": reading.p, "choice": reading.choice, "mass": reading.mass,
                                 "tokens": tokens, "seconds": round(time.perf_counter() - t0, 2)}) + "\n")
            fh.flush()
    print("local arm done")


def run_manager() -> None:
    from chimera.core.supervisor import Manager
    from chimera.providers import LLMGateway

    out = HERE / "results" / "manager.jsonl"
    seen = done_ids(out)
    spent = 0.0
    if out.exists():
        spent = sum(float(json.loads(x).get("usd") or 0.0) for x in out.read_text(encoding="utf-8").splitlines() if x.strip())

    class Metered:
        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.last_usd = 0.0

        def complete(self, messages: Any, **kwargs: Any) -> Any:
            from chimera.orchestration.receipts import price_completion

            result = self.inner.complete(messages, **kwargs)
            cost = price_completion(result)
            if cost.unpriced:
                # An unpriced call counted as free is how a cap stops capping (B4, #537).
                raise SystemExit(f"no price for {cost.unpriced} — refusing to meter it as US$ 0")
            self.last_usd = float(cost.usd)
            return result

    backend = Metered(LLMGateway())
    manager = Manager(backend, MANAGER_MODEL)
    with out.open("a", encoding="utf-8") as fh:
        for row in corpus():
            if row["id"] in seen:
                continue
            if spent >= USD_CAP:
                raise SystemExit(f"spend cap reached: US$ {spent:.3f}")
            t0 = time.perf_counter()
            try:
                review = manager.review(row["task"], row["answer"])
                verdict = "abstained" if review.abstained else ("approved" if review.approved else "revise")
                error = ""
            except Exception as exc:  # noqa: BLE001 — a failed call is recorded, never guessed
                verdict, error = "error", f"{type(exc).__name__}: {str(exc)[:200]}"
            spent += backend.last_usd
            fh.write(json.dumps({"id": row["id"], "verdict": verdict, "usd": backend.last_usd, "error": error,
                                 "seconds": round(time.perf_counter() - t0, 2)}) + "\n")
            fh.flush()
    print(f"manager arm done, US$ {spent:.4f}")


def _clustered(rows: list[dict[str, Any]], stat: Any) -> tuple[float | None, float | None, float | None]:
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(r)
    tasks = sorted(by_task)
    point = stat(rows)
    rng = random.Random(SEED)
    draws: list[float] = []
    for _ in range(DRAWS):
        sample = [r for _ in tasks for r in by_task[tasks[rng.randrange(len(tasks))]]]
        v = stat(sample)
        if v is not None:
            draws.append(v)
    draws.sort()
    if not draws:
        return point, None, None
    return point, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]


def _overall(rows: list[dict[str, Any]]) -> float | None:
    return auroc([(r["p"], r["y"]) for r in rows if r.get("p") is not None])


def _within(rows: list[dict[str, Any]]) -> float | None:
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(r)
    values = [a for rs in by_task.values() if (a := _overall(rs)) is not None]
    return statistics.fmean(values) if values else None


def _rate(k: int, n: int) -> str:
    if n == 0:
        return "—"
    z = 1.96
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return f"{k}/{n} = {p:.2f} [{centre - half:.2f}, {centre + half:.2f}]"


def report() -> dict[str, Any]:
    base = {r["id"]: {**r, "y": 1 if r["outcome"] >= PASS else 0} for r in corpus()}
    out: dict[str, Any] = {"claimed": len(base), "true": sum(r["y"] for r in base.values())}
    local_path = HERE / "results" / "local.jsonl"
    if local_path.exists():
        rows = []
        for x in local_path.read_text(encoding="utf-8").splitlines():
            if x.strip():
                d = json.loads(x)
                rows.append({**base[d["id"]], **d})
        out["local_n"] = len(rows)
        out["local_auroc"] = _clustered(rows, _overall)
        out["local_within_task_auroc"] = _clustered(rows, _within)
        out["local_tasks_with_both"] = sum(
            1 for t in {r["task_id"] for r in rows}
            if len({r["y"] for r in rows if r["task_id"] == t}) == 2
        )
        pos = [r for r in rows if r["y"] == 1 and r["p"] is not None]
        neg = [r for r in rows if r["y"] == 0 and r["p"] is not None]
        out["local_at_0.5"] = {"TPR": _rate(sum(r["p"] >= 0.5 for r in pos), len(pos)),
                               "FPR": _rate(sum(r["p"] >= 0.5 for r in neg), len(neg))}
        out["local_no_p"] = sum(r["p"] is None for r in rows)
        out["local_seconds"] = round(statistics.fmean(r["seconds"] for r in rows), 2)
    manager_path = HERE / "results" / "manager.jsonl"
    if manager_path.exists():
        rows = [{**base[d["id"]], **d} for d in (json.loads(x) for x in manager_path.read_text(encoding="utf-8").splitlines() if x.strip())]
        counted = [r for r in rows if r["verdict"] in ("approved", "revise")]
        pos = [r for r in counted if r["y"] == 1]
        neg = [r for r in counted if r["y"] == 0]
        out["manager_n"] = len(rows)
        out["manager_other"] = {v: sum(r["verdict"] == v for r in rows) for v in ("abstained", "error")}
        out["manager_TPR"] = _rate(sum(r["verdict"] == "approved" for r in pos), len(pos))
        out["manager_FPR"] = _rate(sum(r["verdict"] == "approved" for r in neg), len(neg))
        out["manager_usd"] = round(sum(float(r.get("usd") or 0) for r in rows), 4)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=("local", "manager"))
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.arm == "local":
        run_local()
    elif args.arm == "manager":
        run_manager()
    if args.report or not args.arm:
        summary = report()
        (HERE / "results" / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
