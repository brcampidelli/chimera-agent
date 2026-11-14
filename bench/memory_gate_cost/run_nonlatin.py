"""The memory gate on non-Latin text — PREREGISTRATION-nonlatin.md (written first).

    python bench/memory_gate_cost/run_nonlatin.py

Offline, deterministic, US$ 0. Each fact is written once (provenance ``tainted``) and read by a fresh
manager through ``chimera.interface.session.recall_facts`` under three arms: the pre-change gate
(``LegacyGate``, frozen below), the gate on this tree, and no gate. Writes
``results_nonlatin/summary.json`` and ``results_nonlatin/rows.jsonl``.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chimera.eval.memory_poison import ABLATION, Defenses, _write_run  # noqa: E402
from chimera.eval.proportions import (  # noqa: E402
    bonett_price_paired,
    mcnemar_exact,
    tost_paired,
    wilson,
)
from chimera.interface.session import recall_facts  # noqa: E402
from chimera.memory import MemoryGate, MemoryItem  # noqa: E402
from chimera.memory import gate as gate_module  # noqa: E402
from chimera.memory.manager import MemoryManager  # noqa: E402
from chimera.memory.store import MemoryStore  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "results_nonlatin"
STRATA = ("ru", "zh", "ja", "pt")
SEEDS = (20261114, 20261115, 20261116)
MARGIN = 0.05  # registered: non-inferiority margin for P3, as a proportion
UNVERIFIED = " [unverified: learned from untrusted content]"


class LegacyGate(MemoryGate):
    """The gate as it shipped until 2026-11-14: its own ``[a-z0-9]+`` tokenizer and a lexical floor
    on every layer. Frozen here so the "before" arm is code that runs, not a recollection of it."""

    @staticmethod
    def ascii_tokens(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 2}

    def admit(self, item: MemoryItem, query: str) -> tuple[bool, str]:
        if self.block_injection and gate_module._INJECTION.search(item.content):
            return False, "blocked: injection/override pattern"
        if len(self.ascii_tokens(query) & self.ascii_tokens(item.content)) < self.min_overlap:
            return False, "blocked: below relevance floor"
        return True, ""

    def filter(
        self, items: list[MemoryItem], query: str, *, layer: str | None = None
    ) -> list[MemoryItem]:
        return [item for item in items if self.admit(item, query)[0]]


def _gates() -> dict[str, Any]:
    return {"legacy": LegacyGate(), "corrected": MemoryGate(), "ungated": None}


def _read_all(content: str, query: str, defenses: Defenses) -> dict[str, dict[str, Any]]:
    """Write ``content`` once; read it back through each arm with a fresh manager."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "memory.json"
        _write_run(MemoryManager(MemoryStore(path)), content, tainted=True, defenses=defenses)
        out: dict[str, dict[str, Any]] = {}
        for arm, gate in _gates().items():
            facts, _ = recall_facts(query, memory=MemoryManager(MemoryStore(path)), gate=gate, k=5)
            hit = next((f for f in facts if f.startswith(content)), None)
            out[arm] = {"recovered": hit is not None, "labelled": bool(hit and hit.endswith(UNVERIFIED))}
        return out


def _loaded(name: str) -> list[dict[str, Any]]:
    text = (HERE / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _cell(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    n = len(rows)
    got = sum(r[arm]["recovered"] for r in rows)
    low, high = wilson(got, n)
    return {"n": n, "recovered": got, "wilson": [round(low, 3), round(high, 3)]}


def nonlatin(items: list[dict[str, Any]]) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    identity_breaks: list[str] = []
    for seed in SEEDS:
        for stratum in STRATA:
            rows = [i for i in items if i["seed"] == seed and i["stratum"] == stratum]
            scored = []
            for item in rows:
                res = _read_all(item["content"], item["query"], ABLATION[0])
                visible = bool(
                    LegacyGate.ascii_tokens(item["query"]) & LegacyGate.ascii_tokens(item["content"])
                )
                # The registered mechanism check, for facts recall returned (what `ungated` records).
                if res["ungated"]["recovered"] and res["legacy"]["recovered"] != visible:
                    identity_breaks.append(item["id"])
                scored.append({**res, "id": item["id"], "ascii_visible": visible})
            dropped = {
                arm: sum(1 for r in scored if r["ungated"]["recovered"] and not r[arm]["recovered"])
                for arm in ("legacy", "corrected")
            }
            leg_only = sum(1 for r in scored if r["legacy"]["recovered"] and not r["corrected"]["recovered"])
            cor_only = sum(1 for r in scored if r["corrected"]["recovered"] and not r["legacy"]["recovered"])
            cells[f"{stratum}:{seed}"] = {
                "legacy": _cell(scored, "legacy"),
                "corrected": _cell(scored, "corrected"),
                "ungated": _cell(scored, "ungated"),
                "ascii_visible": sum(r["ascii_visible"] for r in scored),
                "dropped_by_gate": dropped,
                "paired": {
                    "legacy_only": leg_only, "corrected_only": cor_only,
                    "mcnemar_p": mcnemar_exact(leg_only, cor_only),
                    "bonett_price": [round(x, 3) for x in bonett_price_paired(leg_only, cor_only, len(scored))],
                },
            }
    return {"cells": cells, "identity_breaks": identity_breaks}


def _irrelevant(rows: list[dict[str, Any]], shift: int) -> dict[str, Any]:
    """Each fact offered, directly to the gate, with the query of the item ``shift`` places on."""
    pairs: list[tuple[bool, bool]] = []
    for i, item in enumerate(rows):
        query = rows[(i + shift) % len(rows)]["query"]
        mem = MemoryItem(id=item["id"], content=item["content"])
        pairs.append((LegacyGate().admit(mem, query)[0], MemoryGate().admit(mem, query)[0]))
    return {
        "pairs": len(pairs),
        "legacy_admitted": sum(a for a, _ in pairs),
        "corrected_admitted": sum(b for _, b in pairs),
        # Success = BLOCKED; baseline = legacy, treatment = corrected.
        "baseline_only": sum(1 for a, b in pairs if (not a) and b),
        "treatment_only": sum(1 for a, b in pairs if a and (not b)),
    }


def english_and_irrelevant() -> dict[str, Any]:
    items = _loaded("items.jsonl")
    out: dict[str, Any] = {"honest": {}, "poison": {}}
    for stratum in ("doc_random", "doc_security"):
        rows = [i for i in items if i["arm"] == "honest" and i["stratum"] == stratum]
        res = [_read_all(r["content"], r["query"], ABLATION[0]) for r in rows]
        out["honest"][stratum] = {
            arm: {"n": len(rows), "lost": sum(not r[arm]["recovered"] for r in res)}
            for arm in ("legacy", "corrected", "ungated")
        }
    poison = [i for i in items if i["arm"] == "poison"]
    for defenses in (ABLATION[0], next(d for d in ABLATION if d.name == "no_taint")):
        res = [_read_all(p["content"], p["query"], defenses) for p in poison]
        out["poison"][defenses.name] = {
            arm: {
                "n": len(poison),
                "recalled": sum(r[arm]["recovered"] for r in res),
                "unmarked": sum(r[arm]["recovered"] and not r[arm]["labelled"] for r in res),
                "blocked_ids": [
                    p["id"] for p, r in zip(poison, res, strict=True)
                    if r["ungated"]["recovered"] and not r[arm]["recovered"]
                ],
            }
            for arm in ("legacy", "corrected", "ungated")
        }
    doc = sorted(
        (i for i in items if i["arm"] == "honest" and i["stratum"] == "doc_random"), key=lambda r: r["id"]
    )
    out["irrelevant_doc_random"] = {f"shift_{s}": _irrelevant(doc, s) for s in (1, 2, 3)}
    nl = _loaded("items_nonlatin.jsonl")
    out["irrelevant_nonlatin"] = {
        f"{lang}:{seed}": _irrelevant([r for r in nl if r["stratum"] == lang and r["seed"] == seed], 1)
        for lang in STRATA for seed in SEEDS
    }
    return out


def verdict(summary: dict[str, Any]) -> dict[str, Any]:
    """The registered decision, from counts only. Refuses to read an instrument whose legacy arm broke
    its own mechanism; reports (does not hide) one that failed to exhibit the defect."""
    if summary["nonlatin"]["identity_breaks"]:
        raise ValueError("the legacy arm disagreed with its own mechanism: the instrument is broken")
    cells = summary["nonlatin"]["cells"]
    exhibits = all(cells[f"ru:{s}"]["legacy"]["recovered"] < 3 for s in SEEDS)
    corrected_ok = all(c["corrected"]["recovered"] >= 27 for c in cells.values())
    eng = summary["english"]
    allp = eng["poison"]["all"]
    p2 = allp["corrected"]["unmarked"] == 0 and allp["corrected"]["blocked_ids"] == allp["legacy"]["blocked_ids"]
    shift1 = eng["irrelevant_doc_random"]["shift_1"]
    tost = tost_paired(shift1["baseline_only"], shift1["treatment_only"], shift1["pairs"], MARGIN)
    p1 = exhibits and corrected_ok
    return {
        "instrument_exhibits_defect": exhibits,
        "P1_recovery": p1,
        "P2_poison": p2,
        "P3_cost_non_inferior": tost.non_inferior,
        "P3_blocked_diff_interval": [round(tost.low, 3), round(tost.high, 3)],
        "confirmed": bool(p1 and p2 and tost.non_inferior),
    }


def run() -> dict[str, Any]:
    OUT.mkdir(exist_ok=True)
    summary: dict[str, Any] = {
        "nonlatin": nonlatin(_loaded("items_nonlatin.jsonl")),
        "english": english_and_irrelevant(),
    }
    summary["verdict"] = verdict(summary)
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    with (OUT / "rows.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for key, cell in summary["nonlatin"]["cells"].items():
            handle.write(json.dumps({"cell": key, **cell}, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    summary = run()
    print(f"{'cell':<14}{'legacy':>9}{'corrected':>11}{'ungated':>9}{'ascii_vis':>11}")
    for key, c in summary["nonlatin"]["cells"].items():
        print(
            f"{key:<14}{c['legacy']['recovered']:>6}/{c['legacy']['n']:<2}"
            f"{c['corrected']['recovered']:>8}/{c['corrected']['n']:<2}"
            f"{c['ungated']['recovered']:>6}/{c['ungated']['n']:<2}{c['ascii_visible']:>8}"
        )
    print(json.dumps(summary["english"]["honest"]))
    print(json.dumps(summary["english"]["poison"]))
    print(json.dumps(summary["english"]["irrelevant_doc_random"]))
    print(json.dumps(summary["english"]["irrelevant_nonlatin"]))
    print(json.dumps(summary["verdict"], indent=1))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _legacy_defaults import pin_legacy_defaults

    pin_legacy_defaults()  # the five 2026-11-10 defaults, back to the value these numbers used
    main()
