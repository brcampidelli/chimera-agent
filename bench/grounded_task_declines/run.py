"""Run `bench/grounded_task_declines` (PREREGISTRATION.md §3). Paid: luna drafts, Sol on escalation.

Every item is forced through the product's own check — ``build_grounded_answers(settings, gateway)``
and ``GroundedVerifier.verify`` — whatever the classifier says, so the decline rate is read on every
task, not only on the ones the classifier misreads (their count is recorded per item).

Resumable: one line per finished item in ``<out>/runs.jsonl``; an item already there is skipped.
Stops before starting an item once the spend reaches ``--stop-usd``. The key is read by the
product's settings from the ``.env`` of the working directory and is never printed.

    python <worktree>/bench/grounded_task_declines/run.py --out <dir> --limit 6   # stage S0
    python <worktree>/bench/grounded_task_declines/run.py --out <dir>             # stage S1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ITEMS = HERE / "results" / "items.jsonl"
DRAFTER = "openrouter/openai/gpt-6-luna"
#: The drafting turn's system prompt: a neutral assistant line plus the note a gated turn carries
#: in the product (`code_api.py` adds GROUNDED_NOTE to the turn when the classifier reads a
#: question). The agent's full prompt is not reproduced (PREREGISTRATION.md §6).
ASSISTANT = "You are a helpful assistant."


def draft(gateway, item: dict) -> tuple[str, float, str]:
    from chimera.fusion.verified import GROUNDED_NOTE
    from chimera.orchestration.receipts import price_completion

    docs = "\n\n".join(f"[{i}] {t}" for i, t in enumerate(item["excerpts"], 1))
    messages = [
        {"role": "system", "content": f"{ASSISTANT}\n\n{GROUNDED_NOTE}"},
        {"role": "user", "content": f"Attached documents:\n{docs}\n\n{item['message']}"},
    ]
    result = gateway.complete(messages, model=DRAFTER, max_tokens=4000)
    return str(result.content or ""), float(price_completion(result).usd or 0.0), str(result.model or DRAFTER)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0, help="stage S0: only the first N items of each family")
    parser.add_argument("--stop-usd", type=float, default=4.5)
    args = parser.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # The bench never writes the owner's ~/.chimera (identity, decision log): its own home.
    os.environ["CHIMERA_HOME"] = str(out / "home")
    os.environ["CHIMERA_DECISION_BACKEND"] = "local_logprob"
    os.environ["CHIMERA_VERIFIED_ANSWERS"] = "1"
    sys.path.insert(0, str(HERE.parents[1]))

    from chimera.config import get_settings
    from chimera.fusion.grounded_question import is_question
    from chimera.fusion.verified import GroundedTurn, build_grounded_answers
    from chimera.providers import LLMGateway

    settings = get_settings()
    gateway = LLMGateway(settings)
    checker = build_grounded_answers(settings, gateway)
    if checker is None:
        print("verified answers are off in these settings — refusing to run")
        return 2

    items = [json.loads(line) for line in ITEMS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        items = [it for fam in ("T", "C") for it in [x for x in items if x["family"] == fam][: args.limit]]
    log = out / "runs.jsonl"
    done = {json.loads(line)["item_id"] for line in log.read_text(encoding="utf-8").splitlines()} if log.exists() else set()
    spent = sum(json.loads(line)["usd_total"] for line in log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0.0

    for item in items:
        if item["item_id"] in done:
            continue
        if spent >= args.stop_usd:
            print(f"admission stop: US$ {spent:.4f} spent")
            break
        text, usd_draft, served = draft(gateway, item)
        turn = GroundedTurn.make(item["excerpts"], item["message"], ["attachments"])
        from chimera.fusion.decline_language import DECLINES, decline_language

        lang = decline_language(checker.owner_language, item["message"])
        result = checker.verifier.verify(turn, text, escalate=lambda t=turn: checker.redraft(t), decline=DECLINES[lang])
        block = result.receipt()
        row = {
            "item_id": item["item_id"], "family": item["family"], "lang": item["lang"], "kind": item["kind"],
            "message": item["message"], "classifier_reads_question": is_question(item["message"]),
            "draft": text, "draft_model": served, "usd_draft": round(usd_draft, 6),
            "shipped": result.text, "grounded": block,
            "usd_total": round(usd_draft + block["usd_extra"], 6),
        }
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        spent += row["usd_total"]
        print(f"{item['item_id']:40} {block['outcome']:10} p={block['p']} esc={block['escalated']} US$ {spent:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
