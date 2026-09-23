"""Build the Manager bench's corpus from runs that already happened — nothing here spends.

    ~/hb-venv/bin/python bench/manager_p/build_corpus.py [--homes ~/hb-homes] [--out bench/manager_p/results/corpus.jsonl]

Source: the harness factorial (#453, `bench/harness_bench/results/2026-09-13-factorial.jsonl`) — 552
solves by one executor (deepseek-v3.2), each with an oracle outcome in [0, 1]. Its run homes
(`~/hb-homes/<task>-arm-<arm>-r<replica>/runs.jsonl`) hold what the Manager would have been shown:
the task, the worker's final answer, and whether the run CLAIMED success.

One row per solve whose run claimed success — the population the Manager is asked about: "the worker
says it is done; is it?". The label (a true success) is decided by the oracle outcome against a
threshold fixed in PREREGISTRATION.md, not here: this file only joins and records the outcome.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FACTORIAL = REPO / "bench" / "harness_bench" / "results" / "2026-09-13-factorial.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--homes", default=str(Path.home() / "hb-homes"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results" / "corpus.jsonl"))
    args = ap.parse_args()
    homes = Path(args.homes)
    rows = [json.loads(line) for line in FACTORIAL.read_text(encoding="utf-8").splitlines() if line.strip()]
    out: list[dict[str, object]] = []
    missing = claimed_false = 0
    for r in rows:
        home = homes / f"{r['task']}-arm-{r['arm']}-r{r['replica']}"
        runs = home / "runs.jsonl"
        if not runs.exists():
            missing += 1
            continue
        records = [json.loads(x) for x in runs.read_text(encoding="utf-8").splitlines() if x.strip()]
        if not records:
            missing += 1
            continue
        last = records[-1]  # the run that produced the delivered answer
        claimed = str(last.get("success")).strip().lower() == "true"
        if not claimed:
            claimed_false += 1
            continue
        out.append({
            "id": f"{r['task']}/{r['arm']}/r{r['replica']}", "task_id": r["task"], "arm": r["arm"],
            "replica": r["replica"], "outcome": r["outcome"], "task": str(last.get("task") or ""),
            "answer": str(last.get("answer") or ""),
        })
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    print(json.dumps({"solves": len(rows), "claimed_success": len(out), "claimed_failure": claimed_false,
                      "home_missing": missing}, indent=2))


if __name__ == "__main__":
    main()
