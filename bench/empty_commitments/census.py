"""Deterministic lexical census of future-action commitments in one explicit JSON/JSONL file."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

PROMISE = re.compile(
    r"\b(?:i['’]?ll|i will)\s+(?:remind|notify|tell|let you know|follow up|check in)\b"
    r".{0,100}\b(?:tomorrow|later|next week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|in\s+\d+\s+(?:minutes?|hours?|days?|weeks?))\b"
    r"|\b(?:vou te avisar|lembro|vou lembrar|te lembro|vou te chamar|entro em contato)\b"
    r".{0,100}\b(?:amanhã|mais tarde|semana que vem|segunda-feira|terça-feira|quarta-feira|quinta-feira|sexta-feira|sábado|domingo|daqui a\s+\d+\s+(?:minutos?|horas?|dias?|semanas?))\b",
    re.IGNORECASE,
)
REFUSAL = re.compile(r"\b(?:i can['’]?t|cannot|won['’]?t|unable to|não posso|nao posso|não consigo|nao consigo)\b.{0,60}\b(?:remind|schedule|avisar|lembrar|agendar)\b", re.IGNORECASE)
SCHEDULERS = {"schedule_once", "schedule_cron", "schedule_event", "schedule_webhook", "cron", "scheduler"}


def _messages(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("messages", "chat_history", "history"):
        if isinstance(value.get(key), list):
            return [x for x in value[key] if isinstance(x, dict)]
    if isinstance(value.get("turns"), list):
        out: list[dict[str, Any]] = []
        for turn in value["turns"]:
            if isinstance(turn, dict):
                out.extend(_messages(turn))
        return out
    return [value]


def _tool_names(message: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    calls = message.get("tool_calls") or message.get("tools") or []
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(calls, list):
        for call in calls:
            if isinstance(call, str):
                found.append((call.lower(), {}))
            elif isinstance(call, dict):
                fn = call.get("function") if isinstance(call.get("function"), dict) else call
                name = str(fn.get("name") or call.get("name") or "").lower()
                result = call.get("result") or call.get("output") or {}
                found.append((name, result if isinstance(result, dict) else {"result": result}))
    return found


def _failed(result: dict[str, Any]) -> bool:
    return result.get("success") is False or result.get("ok") is False or str(result.get("status", "")).lower() in {"error", "failed", "failure", "rejected", "refused"}


def classify(messages: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {"empty": 0, "false_claim": 0, "unanchored": 0, "over_refusal": 0}
    replies = 0
    for message in messages:
        role = str(message.get("role") or message.get("speaker") or "").lower()
        if role not in {"assistant", "bot", "ai"}:
            continue
        text = str(message.get("content") or message.get("text") or message.get("reply") or "")
        if not text.strip():
            continue
        replies += 1
        calls = _tool_names(message)
        sched = [(name, result) for name, result in calls if name in SCHEDULERS]
        if PROMISE.search(text):
            if not sched:
                totals["empty"] += 1
            elif any(_failed(result) for _, result in sched):
                totals["false_claim"] += 1
            elif not re.search(r"\b(?:tomorrow|later|next week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|in\s+\d+|amanhã|mais tarde|semana que vem|daqui a\s+\d+)\b", text, re.I):
                totals["unanchored"] += 1
        elif REFUSAL.search(text):
            totals["over_refusal"] += 1
    return {"assistant_replies": replies, "counts": totals, "rates": {k: v / replies if replies else 0.0 for k, v in totals.items()}, "patterns": {"promise": PROMISE.pattern, "refusal": REFUSAL.pattern}}


def read_records(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    else:
        loaded = json.loads(raw)
        records = loaded if isinstance(loaded, list) else [loaded]
    messages: list[dict[str, Any]] = []
    for record in records:
        messages.extend(_messages(record))
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = classify(read_records(args.path))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
