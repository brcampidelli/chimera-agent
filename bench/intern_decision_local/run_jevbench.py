"""Intern-Decision-4B on the 231 public JevBench items — see PREREGISTRATION.md (written first).

    python -m bench.intern_decision_local.run_jevbench --jevbench PATH/TO/jevbench [--smoke]

Talks to the sidecar (``server.py``) on localhost. The items, the pins and the scoring are
``bench/jevbench_local``'s: the clone must sit at the pinned commit with the pinned bytes, and the
score is JevBench's own ``score_task``. Each item is sent the way the vendor's evaluator sends it
(``state`` unchanged, ``questions = {"decision": item.question}``). A failed request is scored
invalid — wrong — as JevBench counts it. One row per item; a rerun skips the items already written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.jevbench_local.run import load, pinned  # noqa: E402

OUT = Path(__file__).resolve().parent / "results" / "jevbench.jsonl"
URL = "http://127.0.0.1:8765/v1/decisions"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jevbench", type=Path, required=True)
    ap.add_argument("--smoke", action="store_true", help="3 items, printed raw, nothing written")
    args = ap.parse_args()
    clone = args.jevbench.resolve()
    pinned(clone)
    sys.path.insert(0, str(clone))
    from jevbench.scoring import score_task  # noqa: PLC0415 — their code, from the pinned clone

    items = load(clone)
    if args.smoke:
        items = [items[0], items[100], items[-1]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if OUT.exists() and not args.smoke:
        done = {
            json.loads(line)["id"]
            for line in OUT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    client = httpx.Client(timeout=600.0)
    print(f"items={len(items)} done={len(done)}", flush=True)
    sink = None if args.smoke else OUT.open("a", encoding="utf-8")
    for n, item in enumerate(items, 1):
        if item["id"] in done:
            continue
        labels = [str(x) for x in item["labels"]]
        row = {
            "id": item["id"],
            "file": item["file"],
            "family": item.get("family"),
            "type": item["question"]["type"],
            "expected": item.get("expected"),
            "labels": labels,
        }
        try:
            r = client.post(
                URL, json={"state": item["state"], "questions": {"decision": item["question"]}}
            )
            r.raise_for_status()
            data = r.json()
            answer = data["answers"]["decision"]
            probs = {label: float(answer["probabilities"].get(label, 0.0)) for label in labels}
            row.update(
                probs=probs,
                raw_probabilities=answer["probabilities"],
                input_tokens=data["usage"]["input_tokens"],
                ms=data["timing"].get("server_ms"),
            )
        except Exception as exc:  # noqa: BLE001 — recorded, scored invalid
            probs = None
            row["halt"] = str(exc)[:300]
        task = SimpleNamespace(
            **{k: item[k] for k in ("id", "question", "labels", "expected") if k in item},
            family=item.get("family"),
        )
        score = (
            score_task(probs, task)
            if probs is not None
            else {"valid": False, "correct": False, "error": row.get("halt")}
        )
        row.update(
            valid=bool(score.get("valid")),
            correct=bool(score.get("correct")),
            predicted=score.get("predicted"),
        )
        if args.smoke:
            print(
                json.dumps(
                    {
                        k: row.get(k)
                        for k in (
                            "id",
                            "file",
                            "expected",
                            "probs",
                            "predicted",
                            "correct",
                            "input_tokens",
                            "ms",
                            "halt",
                        )
                    },
                    ensure_ascii=False,
                )
            )
            continue
        assert sink is not None
        sink.write(json.dumps(row, ensure_ascii=False) + "\n")
        sink.flush()
        if n % 25 == 0:
            print(f"  {n}/{len(items)}", flush=True)
    if sink:
        sink.close()


if __name__ == "__main__":
    main()
