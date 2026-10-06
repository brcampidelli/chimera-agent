"""Offline S30-56 baseline and deferred model harness.

    python bench/memory_staleness/run.py --baseline
    python bench/memory_staleness/run.py --check-fake
    python bench/memory_staleness/run.py --model --backend qwen3:4b  # NOT authorized in this task

The --model path deliberately requires an explicit confirmation flag and is never used by --check-fake.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Protocol

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from bench.memory_staleness.items import OPINIONS, UPDATES, OpinionItem, UpdateItem  # noqa: E402
from chimera.memory.manager import MemoryManager  # noqa: E402
from chimera.memory.store import MemoryStore  # noqa: E402


class Backend(Protocol):
    def complete_case(self, kind: str, facts: list[str], probe: str) -> str: ...


def deterministic_baseline() -> dict[str, object]:
    """Record what today's deterministic memory path does, without constructing a model backend."""
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="chimera-s30-56-") as tmp:
        for item in UPDATES:
            memory = MemoryManager(MemoryStore(Path(tmp) / f"{item.id}.json"))
            prior = memory.add(item.old_fact)
            # Model an extracted new statement with today's deterministic writer but no identity key:
            # manager.remember can deduplicate exact text or update a shared key, not link near-facts.
            memory.remember(item.new_fact)
            current = memory.store.all()
            new_saved = any(item.new_fact.casefold() == hit.content.casefold() for hit in current)
            old_saved = any(hit.id == prior.id for hit in current)
            hits = memory.search(item.probe)
            new_recalled = any(hit.id != prior.id and item.new_fact.casefold() == hit.content.casefold()
                               for hit in hits)
            old_recalled = any(hit.id == prior.id for hit in hits)
            rows.append({"id": item.id, "type": item.type, "new_saved": new_saved,
                         "new_recalled": new_recalled, "old_saved": old_saved,
                         "old_recalled_as_current": old_recalled,
                         "outcome": "stale" if old_saved else "updated"})
    misses = sum(not bool(row["new_recalled"]) for row in rows)
    stale = sum(bool(row["old_saved"]) for row in rows)
    result = {"cases": len(rows), "new_recalled": len(rows) - misses,
              "old_recalled_as_current": sum(bool(row["old_recalled_as_current"]) for row in rows),
              "old_still_saved": stale, "misses": misses,
        "wrong_updates": sum(bool(row["old_recalled_as_current"]) for row in rows), "rows": rows,
              "method": "seed old fact; manager.remember(new fact) without key (no model); inspect store and downstream search"}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


#: Function words dropped from the changed-value sets, so "an iPhone" needs "iphone", not "an".
_FUNCTION_WORDS = frozenset({"a", "an", "the", "in", "on", "at", "of", "to", "is", "my", "i", "every"})


def _words(text: str) -> set[str]:
    return set(re.findall(r"[\w+]+", text.casefold())) - _FUNCTION_WORDS


def changed_values(item: UpdateItem) -> tuple[set[str], set[str]]:
    """The words that carry the change: in the new fact and not the old, and the reverse.

    "I live in Porto." -> "I live in Lisbon." gives ({"lisbon"}, {"porto"}). Grading on the whole
    sentence (the first draft) needed the model to echo "I live in Lisbon." verbatim, so an answer
    "You live in Lisbon." read as a missed update and a wrong update at once.
    """
    new, old = _words(item.new_fact), _words(item.old_fact)
    return new - old, old - new


def model_case(backend: Backend, item: UpdateItem | OpinionItem) -> dict[str, object]:
    if isinstance(item, UpdateItem):
        reply = backend.complete_case("update", [item.old_fact, item.utterance], item.probe)
        new_value, old_value = changed_values(item)
        said = _words(reply)
        correct = bool(new_value) and new_value <= said
        # Conservative, as registered: the old value anywhere in the answer counts against it.
        old = bool(old_value) and old_value <= said
        return {"id": item.id, "kind": item.type, "reply": reply, "new_recalled": correct,
                "old_recalled_as_current": old, "wrong_update": old or (not correct)}
    reply = backend.complete_case("opinion", [item.opinion], item.probe)
    return {"id": item.id, "kind": "opinion", "reply": reply,
            "preserves_attribution": item.expected_attribution.casefold() in reply.casefold()}


class _GatewayBackend:
    """Real Qwen backend; instantiated only by the explicitly authorized --model path."""
    def __init__(self, model: str) -> None:
        from chimera.providers.gateway import LLMGateway

        self.gateway = LLMGateway()
        self.model = model

    def complete_case(self, kind: str, facts: list[str], probe: str) -> str:
        from chimera.providers.gateway import Message

        instructions = (
            "You are being evaluated for memory recall. Answer only from the supplied memory. "
            "For updates, use the latest user statement and do not present superseded values as current. "
            "For opinions, preserve explicit attribution to the user and do not state it as verified fact. "
            "If memory does not answer, say so."
        )
        payload = json.dumps({"case": kind, "memory": facts, "question": probe}, ensure_ascii=False)
        result = self.gateway.complete(
            [Message(role="system", content=instructions), Message(role="user", content=payload)],
            model=self.model, temperature=0.0, max_tokens=256, thinking=False,
        )
        text = str(getattr(result, "content", "") or "")
        if not text.strip():
            # An empty reply is the instrument failing, not an answer that missed the update.
            raise RuntimeError(f"empty completion from {self.model}: instrument error")
        return text


class _FakeBackend:
    """Deterministic fixture: no gateway, environment lookup, or network access."""
    def __init__(self) -> None:
        self.calls = 0
        self.updates = {item.utterance: item.new_fact for item in UPDATES}

    def complete_case(self, kind: str, facts: list[str], probe: str) -> str:
        self.calls += 1
        if kind == "update":
            return self.updates[facts[1]]
        return f"The user's opinion is: {facts[0]}"

def evaluate(backend: Backend, replicas: int) -> list[dict[str, object]]:
    """Run every frozen update and opinion item for each requested replica."""
    if replicas < 1:
        raise ValueError("replicas must be at least one")
    return [
        {"replica": replica, **model_case(backend, item)}
        for replica in range(1, replicas + 1)
        for item in (*UPDATES, *OPINIONS)
    ]


def check_fake() -> None:
    backend = _FakeBackend()
    rows = evaluate(backend, replicas=2)
    assert backend.calls == 150
    assert len(rows) == 150
    assert all(row.get("new_recalled", True) for row in rows if row["kind"] in {"I", "II"})
    assert all(row.get("preserves_attribution", False) for row in rows if row["kind"] == "opinion")
    assert len(UPDATES) == 45 and len(OPINIONS) == 30
    print("fake backend: 150 fixture responses (75 items × 2 replicas); grading passed; no model/network")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--check-fake", action="store_true")
    parser.add_argument("--model", action="store_true")
    # The gateway resolves a provider from the prefix; a bare "qwen3:4b" is not a local Ollama route.
    parser.add_argument("--backend", default="ollama_chat/qwen3:4b")
    parser.add_argument("--replicas", type=int, default=2)
    parser.add_argument("--confirm-model-run", action="store_true")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("results") / "model.json")
    args = parser.parse_args()
    if args.baseline:
        deterministic_baseline()
    elif args.check_fake:
        check_fake()
    elif args.model:
        if not args.confirm_model_run:
            parser.error("model execution is deferred; explicit --confirm-model-run required")
        backend = _GatewayBackend(args.backend)
        rows = evaluate(backend, replicas=args.replicas)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"model": args.backend, "replicas": args.replicas,
                                        "items": rows}, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        print(f"wrote {len(rows)} model responses to {args.out}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
