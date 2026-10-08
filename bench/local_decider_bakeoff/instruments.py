"""The two registered instruments, pointed at a local endpoint — see PREREGISTRATION.md.

A · JevBench-231: items, pins and scoring are ``bench/jevbench_local``'s (pinned clone, sha256
checks, JevBench's own ``score_task``); each item is sent as ``{"state": item.state, "questions":
{"decision": item.question}}`` — plus the item's ``labels`` order for an endpoint that reads it.

B · Governance: ``bench/jev_decisions/run.py`` arm J, unchanged but for the transport — ``run.jev``
is replaced by a call to the local endpoint that returns the same row fields, ``usd`` 0.

Both apply guard 4 (valid probabilities) to every answer: one violation aborts the run.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.jev_decisions import run as arm_j  # noqa: E402
from bench.jevbench_local.run import load, pinned  # noqa: E402
from bench.local_decider_bakeoff.common import (  # noqa: E402
    GuardError,
    check_distribution,
    probs_of_answer,
)

VERDICT_LABELS = ["BLOCK", "REVIEW", "ALLOW"]


def _post(client: httpx.Client, url: str, body: dict[str, Any]) -> dict[str, Any]:
    r = client.post(url, json=body)
    r.raise_for_status()
    data: dict[str, Any] = r.json()
    return data


def jevbench(url: str, out: Path, clone: Path, *, send_labels: bool, smoke: bool = False) -> int:
    """Returns the number of halts in this call (Amendment 1, guards 8 and 9 read it)."""
    clone = clone.resolve()
    pinned(clone)
    sys.path.insert(0, str(clone))
    from jevbench.scoring import score_task  # noqa: PLC0415 — their code, from the pinned clone

    items = load(clone)
    if smoke:
        items = [items[0], items[100], items[-1]]
    done: set[str] = set()
    if out.exists() and not smoke:
        done = {json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}
    client = httpx.Client(timeout=600.0)
    print(f"[jevbench] items={len(items)} done={len(done)} -> {out if not smoke else 'smoke'}", flush=True)
    sink = None if smoke else out.open("a", encoding="utf-8")
    halts = 0
    try:
        for n, item in enumerate(items, 1):
            if item["id"] in done:
                continue
            labels = [str(x) for x in item["labels"]]
            kind = item["question"]["type"]
            question = dict(item["question"])
            if send_labels:
                question["labels"] = labels
            row: dict[str, Any] = {
                "id": item["id"], "file": item["file"], "family": item.get("family"), "type": kind,
                "expected": item.get("expected"), "labels": labels,
            }
            t0 = time.perf_counter()
            try:
                data = _post(client, url, {"state": item["state"], "questions": {"decision": question}})
            except httpx.HTTPError as exc:  # recorded and scored invalid, as JevBench counts it
                data = None
                row["halt"] = str(exc)[:300]
                halts += 1
            row["seconds"] = round(time.perf_counter() - t0, 3)
            probs = None
            if data is not None:
                answer = data["answers"]["decision"]
                probs = probs_of_answer(answer, kind, labels, item["id"])  # guard 4, aborts on violation
                usage = data.get("usage") or {}
                row.update(probs=probs, raw_answer=answer, input_tokens=usage.get("input_tokens"),
                           fallback_letters=usage.get("fallback_letters"))
            task = SimpleNamespace(
                **{k: item[k] for k in ("id", "question", "labels", "expected") if k in item}, family=item.get("family")
            )
            score = score_task(probs, task) if probs is not None else {"valid": False, "correct": False}
            row.update(valid=bool(score.get("valid")), correct=bool(score.get("correct")), predicted=score.get("predicted"))
            if smoke:
                print("  RAW", json.dumps({k: row.get(k) for k in ("id", "file", "type", "expected", "raw_answer", "predicted",
                                                               "correct", "input_tokens", "seconds", "halt")}, ensure_ascii=False))
                continue
            assert sink is not None
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            sink.flush()
            if n % 25 == 0:
                print(f"  {n}/{len(items)}", flush=True)
    finally:
        if sink:
            sink.close()
    return halts


def _local_jev(url: str, model: str) -> Any:
    client = httpx.Client(timeout=600.0)
    tripped: list[GuardError] = []

    def call(_client: httpx.Client, state: str, questions: dict[str, Any] | None = None) -> dict[str, Any]:
        if tripped:
            # the pool drains its queue before the abort propagates: skip the model for the rest
            raise SystemExit(tripped[0].code)
        t0 = time.perf_counter()
        data = _post(client, url, {"model": model, "state": state, "questions": questions or arm_j.QUESTIONS})
        seconds = round(time.perf_counter() - t0, 3)
        danger, verdict = data["answers"]["danger"], data["answers"]["verdict"]
        vp = {str(k): float(v) for k, v in (verdict.get("probabilities") or {}).items()}
        # GuardError is a SystemExit: run._one records an Exception as a halt, but this aborts the run
        try:
            p = probs_of_answer(danger, "noul", ["yes", "no"], "governance danger")["yes"]
            check_distribution(vp, VERDICT_LABELS, "governance verdict")
        except GuardError as exc:
            tripped.append(exc)
            raise
        usage = data.get("usage") or {}
        return {
            "p": p, "verdict": verdict.get("choice") or max(vp, key=lambda k: vp[k]), "probs": vp,
            "confidence": verdict.get("confidence"), "usd": 0.0,
            "in_tokens": usage.get("input_tokens"), "out_tokens": usage.get("output_tokens"),
            "provider": "local", "model": model, "seconds": seconds,
            "raw": {"danger": danger, "verdict": verdict, "usage": usage, "timing": data.get("timing")},
        }

    return call


def governance(url: str, model: str, out: Path, wrapper_set: str, *, smoke: bool = False) -> None:
    call = _local_jev(url, model)
    if smoke:
        for item in arm_j.two_sided_items()[:2] + arm_j.oats_items()[:1]:
            res = call(httpx.Client(), item["state"])
            print("  RAW", item["id"], item.get("label"), json.dumps(res["raw"], ensure_ascii=False)[:600], f"{res['seconds']}s", flush=True)
        return
    arm_j.jev = call  # the only change to the arm J design
    arm_j.JEV_MODEL = model
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"[governance {wrapper_set}] -> {out}", flush=True)
    arm_j.run(out, httpx.Client(), None, reps_j=5, workers=1, arms="J", wrapper_set=wrapper_set)
