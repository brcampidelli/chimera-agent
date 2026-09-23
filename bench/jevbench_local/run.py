"""Our local decision backend on the 231 public JevBench items — see PREREGISTRATION.md (written first).

    python -m bench.jevbench_local.run --jevbench PATH/TO/jevbench --arm ctx
    python -m bench.jevbench_local.run --jevbench PATH/TO/jevbench --arm ship

``PATH/TO/jevbench`` is a clone of github.com/fstandhartinger/jevbench (MIT). The runner refuses any
other commit than the pinned one and any dataset whose sha256 differs, and imports the benchmark's
own scoring from that clone, so the number is computed by their code, not ours.

Local only (Ollama ``qwen3:4b``), US$ 0. One row per item in ``results/<arm>.jsonl``; a rerun skips the
items already written.

How an item becomes our question (the product path, ``chimera/decisions``):

* ``noul`` → :class:`Noul` with the item's ``true``/``false`` criteria — the product asks it as ``yes`` /
  ``no`` in that order; the JevBench labels are ``no``/``yes``, the same two words, so the shares map one
  to one.
* ``choice`` → :class:`Choice` over the item's ``labels`` in the item's order, criteria verbatim.
* ``score`` → :class:`Score` whose levels are the item's ``labels`` (``"0"``…) and whose criteria are the
  item's list, level by level.

The JSON key the model writes is ``answer`` for every item. Nothing here was tuned against a Jev output
(MCA §2.3(b)): the instrument is the shipped one, and the only thing the arms change is ``num_ctx``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chimera.decisions import Choice, Noul, Score, as_choice  # noqa: E402
from chimera.decisions.contract import Question  # noqa: E402
from chimera.decisions.lint import errors  # noqa: E402
from chimera.decisions.local import LocalLogprobBackend  # noqa: E402

COMMIT = "2fa63fa3226cb369795525ed011800f57dcbd894"
FILES = {  # file -> (sha256 prefix, items)
    "original": ("b2abe9ac8cabd953", 72),
    "easy": ("314cd493a4c080fc", 48),
    "hard": ("8c8f1efb5a04b701", 111),
}
KEY = "answer"
NUM_CTX = 16384
ARMS = ("ctx", "ship")
OUT_DIR = Path(__file__).resolve().parent / "results"
BASE_URL = "http://localhost:11434"


def pinned(clone: Path) -> None:
    """Refuse anything but the pre-registered commit and bytes."""
    head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if head != COMMIT:
        raise SystemExit(f"jevbench clone is at {head}, the pre-registration pins {COMMIT}")
    for name, (prefix, _) in FILES.items():
        digest = hashlib.sha256((clone / "datasets" / "public" / f"{name}.jsonl").read_bytes()).hexdigest()
        if not digest.startswith(prefix):
            raise SystemExit(f"{name}.jsonl sha256 {digest[:16]} != pinned {prefix}")


def load(clone: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name, (_, count) in FILES.items():
        rows = [json.loads(line) for line in (clone / "datasets" / "public" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(rows) != count:
            raise SystemExit(f"{name}.jsonl has {len(rows)} items, the pre-registration says {count}")
        for row in rows:
            if row.get("split") != "public" or (row.get("provenance") or {}).get("license") != "MIT":
                raise SystemExit(f"{row.get('id')}: not a public MIT item")
            items.append({**row, "file": name})
    return items


def question_of(item: dict[str, Any]) -> Question:
    q = item["question"]
    kind, instructions, criteria, labels = q["type"], q["instructions"], q.get("criteria") or {}, [str(x) for x in item["labels"]]
    if kind == "noul":
        if sorted(labels) != ["no", "yes"]:
            raise ValueError(f"{item['id']}: noul labels {labels}")
        return Noul(KEY, instructions, criteria=dict(criteria))
    if kind == "choice":
        return Choice(KEY, instructions, tuple(labels), criteria={k: str(v) for k, v in dict(criteria).items()})
    if kind == "score":
        if not isinstance(criteria, list) or len(criteria) != len(labels):
            raise ValueError(f"{item['id']}: score criteria do not line up with the levels")
        return Score(KEY, instructions, tuple(labels), criteria=dict(zip(labels, (str(c) for c in criteria), strict=True)))
    raise ValueError(f"{item['id']}: unknown question type {kind}")


def probs_of(shares: dict[str, float] | None, labels: list[str]) -> dict[str, float] | None:
    """The distribution handed to JevBench's ``score_task``: our shares keyed by the item's labels, or
    ``None`` when the backend had no reading (scored invalid, i.e. wrong — as JevBench counts it)."""
    if not shares:
        return None
    return {label: float(shares.get(label, 0.0)) for label in labels}


def state_text(state: Any) -> str:
    """JevBench's own convention (every adapter in ``jevbench/adapters``): a structured state is sent
    as ``json.dumps(state, ensure_ascii=False)``."""
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)


def body_of(backend: LocalLogprobBackend, state: str, question: Choice, arm: str) -> dict[str, Any]:
    body = backend.body(state, question)
    if arm == "ctx":
        body["options"]["num_ctx"] = NUM_CTX
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jevbench", type=Path, required=True)
    parser.add_argument("--arm", choices=ARMS, required=True)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--limit", type=int, default=0, help="first N items only (smoke)")
    args = parser.parse_args()

    clone = args.jevbench.resolve()
    pinned(clone)
    sys.path.insert(0, str(clone))
    from jevbench.scoring import score_task  # noqa: PLC0415 — their code, from the pinned clone

    items = load(clone)
    if args.limit:
        items = items[: args.limit]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.arm}.jsonl"
    done = {json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()} if out.exists() else set()

    backend = LocalLogprobBackend(args.base_url, timeout_s=300.0)
    build = backend.resolved_model()
    print(f"arm={args.arm} items={len(items)} done={len(done)} build={build or '?'}", flush=True)
    client = httpx.Client()
    shown = 0
    with out.open("a", encoding="utf-8") as sink:
        for n, item in enumerate(items, 1):
            if item["id"] in done:
                continue
            question = question_of(item)
            choice = as_choice(question)
            labels = [str(x) for x in item["labels"]]
            started = time.perf_counter()
            response = client.post(f"{backend.base_url}/api/chat", json=body_of(backend, state_text(item["state"]), choice, args.arm), timeout=300.0)
            response.raise_for_status()
            data = response.json()
            seconds = time.perf_counter() - started
            prompt_tokens = int(data.get("prompt_eval_count") or 0)
            if args.arm == "ctx" and not 0 < prompt_tokens < NUM_CTX - 64:
                raise SystemExit(f"{item['id']}: prompt_eval_count {prompt_tokens} — truncated or unread under num_ctx {NUM_CTX}")
            reading = backend.read(data, choice, resolved_model=build)
            probs = probs_of(reading.shares, labels)
            task = SimpleNamespace(labels=labels, expected=item["expected"], question=item["question"])
            scored = score_task(probs, task) if probs is not None else {"valid": False, "correct": False, "predicted": None, "error": "no reading"}
            row = {
                "id": item["id"], "file": item["file"], "family": item.get("family"), "type": item["question"]["type"],
                "arm": args.arm, "expected": item["expected"], "labels": labels,
                # ``choice`` is the label the model WROTE, kept apart from ``predicted``: a first-token
                # collision between two options (study 21 A4) leaves no reading even when it is right
                "choice": reading.choice, "shares": reading.shares, "mass": reading.mass,
                "valid": bool(scored.get("valid")), "correct": bool(scored.get("correct")), "predicted": scored.get("predicted"),
                "confidence": max(scored["probs"].values()) if scored.get("probs") else None,
                "error": scored.get("error"),
                "prompt_eval_count": prompt_tokens, "seconds": round(seconds, 3),
                "lint": [f.message for f in errors(question)], "build": build, "raw": reading.raw,
            }
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            sink.flush()
            if shown < 3:  # the Bee rule: read three raw outputs with the eyes before trusting a number
                shown += 1
                print(f"  RAW {item['id']}: {reading.raw!r} shares={reading.shares} tokens={prompt_tokens}", flush=True)
            if n % 20 == 0:
                print(f"  {n}/{len(items)}", flush=True)
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
