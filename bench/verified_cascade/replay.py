"""Read a run directory back: the drafts, readings and labels per item, and every policy replayed over
them (§4.1: the arms differ only in which logged call's output they would ship). Shared by ``run.py``
(its gates and escalation lists) and ``report.py``. Offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.verified_cascade.common import RESULTS, number_check_fires, read_jsonl
from bench.verified_cascade.harness import (
    G1,
    G2,
    LUNA,
    SOL,
    THRESHOLD,
    CallLog,
    Outcome,
    Reading,
    final_label,
    lexical,
    verified,
)

GRADERS = {"g1": G1, "g2": G2}
DRAFT_MODEL = {"d1": LUNA, "d2": LUNA, "d3": LUNA, "f1": SOL, "f2": SOL}
SHORT = {LUNA: "luna", SOL: "sol"}


def draft_key(item_id: str, draw: str) -> str:
    return f"draft|{SHORT[DRAFT_MODEL[draw]]}|{item_id}|{draw}"


def read_key(verifier: str, target: str, draw: str = "") -> str:
    return f"read|{verifier}|{target}|{draw}" if draw else f"read|{verifier}|{target}"


def grade_key(grader: str, target: str, draw: str = "", prefix: str = "grade") -> str:
    return f"{prefix}|{grader}|{target}|{draw}" if draw else f"{prefix}|{grader}|{target}"


def load_items() -> list[dict[str, Any]]:
    return sorted(read_jsonl(RESULTS / "items.jsonl"), key=lambda i: i["order"])


def load_vslice() -> list[dict[str, Any]]:
    return read_jsonl(RESULTS / "verifier_slice.jsonl")


def load_excerpts() -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in read_jsonl(RESULTS / "excerpts.jsonl")}


@dataclass
class RunData:
    out: Path
    items: list[dict[str, Any]] = field(default_factory=load_items)
    ex: dict[str, dict[str, Any]] = field(default_factory=load_excerpts)

    def __post_init__(self) -> None:
        self.log = CallLog(self.out / "calls.jsonl")
        self.by_id = {i["item_id"]: i for i in self.items}
        self.adjudications: dict[str, str] = {}
        path = self.out / "adjudications.jsonl"
        if path.exists():
            for row in read_jsonl(path):
                self.adjudications[row["target"]] = row["label"]

    def texts(self, item: dict[str, Any]) -> list[str]:
        return [self.ex[e]["text"] for e in item["excerpt_ids"]]

    def text(self, item_id: str, draw: str) -> str | None:
        row = self.log.get(draft_key(item_id, draw))
        return str(row.get("text", "")) if row and row.get("status") == "ok" else None

    def reading(self, verifier: str, item_id: str, draw: str) -> Reading | None:
        row = self.log.get(read_key(verifier, item_id, draw))
        if not row or row.get("status") != "ok":
            return None
        return Reading(row.get("choice"), row.get("p"))

    def votes(self, target: str, draw: str = "", prefix: str = "grade") -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for g in GRADERS:
            row = self.log.get(grade_key(g, target, draw, prefix))
            out[g] = row.get("label") if row and row.get("status") == "ok" else None
        return out

    def number_fires(self, item_id: str, draw: str) -> bool:
        text = self.text(item_id, draw)
        item = self.by_id[item_id]
        return text is not None and number_check_fires(text, self.texts(item), item["question"])

    def label(self, item_id: str, draw: str) -> tuple[str | None, str]:
        if self.text(item_id, draw) is None:
            return None, "no_draft"
        target = f"{item_id}|{draw}"
        return final_label(self.votes(item_id, draw), self.adjudications.get(target), self.number_fires(item_id, draw))

    def cost(self, key: str) -> float:
        row = self.log.get(key)
        return float(row.get("usd") or 0.0) if row else 0.0


def outcomes(rd: RunData, item: dict[str, Any]) -> dict[str, tuple[Outcome, dict[str, Any]]]:
    """Every registered policy on one item (§4.2), primary and secondary."""
    iid = item["item_id"]
    d1, d2, f1 = rd.text(iid, "d1"), rd.text(iid, "d2"), rd.text(iid, "f1")
    has_f1 = f1 is not None
    out: dict[str, tuple[Outcome, dict[str, Any]]] = {}
    out["A"] = (Outcome("d1" if d1 is not None else None, False, ("d1",)), {})
    out["A2"] = (Outcome("d2" if d2 is not None else None, False, ("d2",)), {})
    out["C"] = (Outcome("f1" if has_f1 else None, False, ("f1",)), {})
    for arm, ver in (("B", "jev"), ("D", "local")):
        for first, suffix in (("d1", ""), ("d2", "2")):
            r1 = rd.reading(ver, iid, first) if rd.text(iid, first) is not None else None
            r2 = rd.reading(ver, iid, "f1") if has_f1 else None
            out[arm + suffix] = (verified(first, r1, r2, ver, has_f1=has_f1), {})
            if suffix:
                continue
            out[arm + "_decl"] = (verified(first, r1, r2, ver, has_f1=has_f1, declined_ships=True), {})
            for t in (0.5, 0.9):
                out[f"{arm}@{t}"] = (verified(first, r1, r2, ver, has_f1=has_f1, threshold=t), {})
    if d1 is not None and d2 is not None:
        out["L"] = lexical(d1, d2, f1)
    else:
        out["L"] = (Outcome(None, False, ("d1", "d2")), {})
    l1, _ = rd.label(iid, "d1")
    lf, _ = rd.label(iid, "f1") if has_f1 else (None, "no_draft")
    if l1 == "correct":
        oracle = Outcome("d1", False, ("d1", "f1"))
    elif lf == "correct":
        oracle = Outcome("f1", True, ("d1", "f1"))
    elif l1 is None or lf is None:
        oracle = Outcome(None, True, ("d1", "f1"))
    else:
        oracle = Outcome("handoff", True, ("d1", "f1"))
    out["oracle"] = (oracle, {})
    return out


def shipped_label(rd: RunData, item: dict[str, Any], outcome: Outcome) -> str | None:
    """What the policy shipped, as a label: a draft's label, ``handoff``, or ``decline``."""
    if outcome.shipped is None:
        return None
    if outcome.shipped in ("handoff", "decline"):
        return outcome.shipped
    label, _ = rd.label(item["item_id"], outcome.shipped)
    return label


def policy_cost(rd: RunData, item: dict[str, Any], outcome: Outcome) -> float:
    iid = item["item_id"]
    total = 0.0
    for call in outcome.calls:
        if ":" in call:
            ver, draw = call.split(":")
            total += rd.cost(read_key(ver, iid, draw))
        else:
            total += rd.cost(draft_key(iid, call))
    return total


def is_wrong(label: str | None) -> bool:
    return label == "wrong"


def escalated_from_d1(rd: RunData, item: dict[str, Any], verifier: str) -> bool:
    r = rd.reading(verifier, item["item_id"], "d1")
    return r is not None and r.choice is not None and not r.accepts(THRESHOLD) and r.choice != "declined"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
