"""A probability on "is this review finding real?" — the aacr-bench rows of `bench/review_judge`.

    python bench/jev_decisions/run_review.py --smoke
    python bench/jev_decisions/run_review.py --run --arms J,V --out <path.jsonl>
    python bench/jev_decisions/report_review.py <path.jsonl>

Registered in `PREREGISTRATION-review.md`. The items, the diff window and the judge's user text are
`bench/review_judge/run_judge.py`'s own, so the rows pair with arms A–E's published numbers. J is the
typed-decision model through OpenRouter's Decisions API; V is the judge model asked for a verbalized
probability with arm C's split rubric, thinking off. `p` is P(the comment is a correct finding) in
both arms — the dataset's label 1.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from bench.jev_decisions.run import (  # noqa: E402
    DECISIONS_URL,
    JEV_MODEL,
    JUDGE_MODEL,
    TIMEOUT,
    _retrying,
)
from bench.review_judge.run_judge import (  # noqa: E402
    Item,
    attach_patches,
    everything,
    load_rows,
    system_prompt,
)
from chimera.orchestration.receipts import price_completion  # noqa: E402

GROUNDS = (
    "The comment is a real finding only if its premise is true of this diff AND it reports a defect "
    "this diff introduces. It is not a real finding when the code it describes is not in the diff; "
    "when a line of the diff contradicts its central claim; when it asserts no defect at all — praise, "
    "a restatement of what the diff does, a preference about naming, wording, style or structure; or "
    "when what it reports was already there before the diff. A true statement is not a finding. A "
    "suggestion to add something is not evidence that its absence is a defect."
)
QUESTIONS: dict[str, Any] = {
    "real_defect": {
        "type": "noul",
        "instructions": (
            "The state holds a code diff (a window around the commented lines) and a review comment "
            "attached to those lines. Does the review comment report a REAL defect that this diff "
            "introduces? " + GROUNDS
        ),
        "criteria": {
            "true": "The comment's premise is true of the diff and it names a defect the diff introduces.",
            "false": "The premise is false of the diff, or the comment names no defect (praise, paraphrase, style, a suggestion), or the issue pre-dates the diff.",
        },
    },
    "verdict": {
        "type": "choice",
        "instructions": (
            "You are the reviewer of a code-review comment. Approve it if it is a real finding about "
            "this diff; reject it otherwise. " + GROUNDS
        ),
        "criteria": {
            "approve": "a real finding: the premise is true of the diff and it reports a defect the diff introduces.",
            "reject": "not a real finding: false premise, no defect asserted, or the issue pre-dates the diff.",
        },
    },
}

VERBALIZED_TAIL = (
    "\n\nAdd one more key to the JSON, before \"verdict\": \"p_real_defect\": a number between 0 and 1, the "
    "probability that this comment reports a real defect introduced by this diff. Before writing a high "
    "probability, actively look for a reason you might be wrong; reviewers have been over-confident in "
    "past evaluations."
)
_JSON = re.compile(r"\{.*\}", re.S)


def user_text(item: Item) -> str:
    """Byte-for-byte the judge bench's user message (`run_judge.ask`)."""
    return (
        f"File: {item.path}\n"
        f"The comment is attached to lines {item.from_line}-{item.to_line} of the new file.\n\n"
        f"--- diff ---\n{item.patch}\n--- end diff ---\n\n"
        f"Review comment:\n{item.note}\n"
    )


def jev(client: httpx.Client, state: str) -> dict[str, Any]:
    body = {"model": JEV_MODEL, "state": state, "questions": QUESTIONS}
    t0 = time.perf_counter()

    def call() -> httpx.Response:
        r = client.post(DECISIONS_URL, json=body, timeout=TIMEOUT)
        r.raise_for_status()
        return r

    data = _retrying(call).json()
    answers = data.get("answers") or {}
    usage = data.get("usage") or {}
    return {
        "p": (answers.get("real_defect") or {}).get("noul"),
        "verdict": (answers.get("verdict") or {}).get("choice"),
        "probs": (answers.get("verdict") or {}).get("probabilities"),
        "confidence": (answers.get("verdict") or {}).get("confidence"),
        "usd": usage.get("cost"), "in_tokens": usage.get("input_tokens"), "out_tokens": usage.get("output_tokens"),
        "model": data.get("model"), "seconds": round(time.perf_counter() - t0, 3),
    }


def verbalized(gateway: Any, state: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    usd = 0.0
    text = ""
    result = None
    for _attempt in range(2):
        result = gateway.complete(
            [{"role": "system", "content": system_prompt("split") + VERBALIZED_TAIL}, {"role": "user", "content": state}],
            model=JUDGE_MODEL, temperature=0.3, max_tokens=2000, thinking=False,
        )
        cost = price_completion(result)
        if not cost.unpriced:
            usd += cost.usd
        text = result.content or ""
        if text.strip():
            break
    p: float | None = None
    verdict: str | None = None
    m = _JSON.search(text)
    if m:
        try:
            obj = json.loads(m.group(0))
            p = float(obj.get("p_real_defect"))
            verdict = str(obj.get("verdict", "")).lower() or None
        except (ValueError, TypeError, AttributeError):
            p = None
    if verdict is None:
        found = re.findall(r"\b(approve|reject)\b", text, re.I)
        verdict = found[-1].lower() if found else None
    return {
        "p": p, "verdict": verdict, "usd": usd or None,
        "in_tokens": getattr(result, "prompt_tokens", None), "out_tokens": getattr(result, "completion_tokens", None),
        "seconds": round(time.perf_counter() - t0, 3), "raw": text[:300],
    }


def items_with_diffs() -> list[Item]:
    items, _dropped = attach_patches(everything(load_rows()))
    return [i for i in items if i.patch]


def _row(arm: str, item: Item) -> dict[str, Any]:
    return {
        "arm": arm, "row_id": item.row_id, "repo": item.repo, "pr": item.pr, "path": item.path,
        "from_line": item.from_line, "source_model": item.source_model, "language": item.language,
        "in_pilot": item.in_pilot, "label": item.label,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--arms", default="J,V")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="first N items (a rehearsal), 0 = all")
    args = ap.parse_args()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    client = httpx.Client(headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    items = items_with_diffs()
    print(f"  {len(items)} items with a diff · {sum(i.in_pilot for i in items)} in the pilot · "
          f"{sum(1 for i in items if i.label == 0)} incorrect / {sum(1 for i in items if i.label == 1)} correct", file=sys.stderr)
    if args.smoke:
        item = items[0]
        state = user_text(item)
        print("state chars:", len(state), "label:", item.label)
        print("J:", json.dumps(jev(client, state), ensure_ascii=False)[:500])
        print("V:", json.dumps(verbalized(gateway, state), ensure_ascii=False)[:500])
        return
    if not args.run or not args.out:
        ap.print_help()
        return
    rng = random.Random(20260919)
    order = items[:]
    rng.shuffle(order)
    if args.limit:
        order = order[: args.limit]
    arms = args.arms.split(",")
    # Resume: rows already in the file are not asked again (the tool that launches long runs kills
    # them at about an hour, and a run that cannot resume is a run that has to be paid twice).
    have: set[tuple[str, int, str, int]] = set()
    if args.out.exists():
        for line in args.out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("arm") != "meta" and not r.get("halt"):
                    have.add((r["arm"], r["row_id"], r["path"], r["from_line"]))
    tasks = [(arm, item) for item in order for arm in arms if (arm, item.row_id, item.path, item.from_line) not in have]
    if have:
        print(f"  resuming: {len(have)} rows already done, {len(tasks)} to go", file=sys.stderr)
    spent = {a: 0.0 for a in arms}
    spent["halts"] = 0
    lock = threading.Lock()
    done = 0
    t0 = time.perf_counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def one(arm: str, item: Item) -> dict[str, Any]:
        row = _row(arm, item)
        try:
            res = jev(client, user_text(item)) if arm == "J" else verbalized(gateway, user_text(item))
        except Exception as exc:  # noqa: BLE001 — a halt, never a verdict
            res = {"halt": str(exc)[:300]}
        row.update(res)
        return row

    with args.out.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(one, arm, item) for arm, item in tasks]
        for fut in as_completed(futures):
            row = fut.result()
            with lock:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                done += 1
                if row.get("usd"):
                    spent[row["arm"]] += float(row["usd"])
                if row.get("halt"):
                    spent["halts"] += 1
                if done % 100 == 0 or done == len(tasks):
                    print(f"  {done}/{len(tasks)} · {time.perf_counter() - t0:.0f}s · " + " ".join(f"{a} ${spent[a]:.4f}" for a in arms) + f" · halts {spent['halts']}", file=sys.stderr)
        fh.write(json.dumps({"arm": "meta", "spent": spent, "rows": done, "items": len(order), "jev_model": JEV_MODEL,
                             "judge_model": JUDGE_MODEL, "questions": QUESTIONS, "seconds": round(time.perf_counter() - t0, 1),
                             "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
