"""Read the results and apply the registered decision rule. No model, no money.

Every interval is a closed form from ``chimera.eval.proportions`` (PROTOCOL section 11). Gate numbers
are the ones written in PREREGISTRATION.md; nothing here chooses a threshold.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from chimera.eval import proportions as pr

PAIR_GATE_PP = -5.0
TOKEN_GATE = 0.50
GUARD_PP = 5.0


def _pairs(rows: list[dict[str, Any]], key: str) -> list[tuple[Any, Any, str]]:
    """``(A value, B value, brief)`` per (brief, rep) where neither arm halted."""
    by: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        by[(r["brief"], r["rep"])][r["arm"]] = r
    out = []
    for (brief, _rep), arms in sorted(by.items()):
        if "A" in arms and "B" in arms and not arms["A"].get("halt") and not arms["B"].get("halt"):
            out.append((arms["A"][key], arms["B"][key], brief))
    return out


def paired_binary(pairs: list[tuple[bool, bool, str]]) -> dict[str, Any]:
    n = len(pairs)
    a_only = sum(1 for a, b, _ in pairs if a and not b)
    b_only = sum(1 for a, b, _ in pairs if b and not a)
    pa, pb = sum(a for a, _, _ in pairs), sum(b for _, b, _ in pairs)
    low, high = pr.bonett_price_paired(a_only, b_only, n)
    per: dict[str, list[float]] = defaultdict(list)
    for a, b, brief in pairs:
        per[brief].append(float(b) - float(a))
    cluster = pr.mean_t_interval([sum(v) / len(v) for v in per.values()]) if len(per) > 1 else None
    return {"n": n, "A": pa / n if n else None, "B": pb / n if n else None,
            "diff_pp": 100 * (pb - pa) / n if n else None,
            "bonett_price_pp": (100 * low, 100 * high), "mcnemar_p": pr.mcnemar_exact(a_only, b_only),
            "a_only": a_only, "b_only": b_only,
            "per_design_t_pp": tuple(100 * x for x in cluster) if cluster else None}


def _load(path: Any) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def model_report(key: str, results: Any) -> dict[str, Any]:
    gen = _load(results / key / "gen.jsonl")
    measured = {(r["brief"], r["rep"], r["arm"]): r for r in _load(results / key / "gen_measured.jsonl")}
    refine = _load(results / key / "refine.jsonl")
    corpus = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((results / key / "corpus").glob("*.json"))]
    out: dict[str, Any] = {"model": key}
    for r in gen:
        r["valid1"] = bool(r["ok"] and r["attempts"] == 1)
        m = measured.get((r["brief"], r["rep"], r["arm"]))
        contrast = m.get("contrast") if m else None
        r["contrast_measured"] = bool(isinstance(contrast, dict) and contrast.get("measured") is True
                                      and type(contrast.get("low")) is int and contrast["low"] >= 0)
        r["contrast_bad"] = bool(r["contrast_measured"] and contrast["low"] > 0)
        r["overflow"] = bool(m and m.get("overflow") is True)
        r["overflow_measured"] = bool(m and isinstance(m.get("overflow"), bool))
    # A refused contrast is a completed measurement with an explicit negative result. A missing
    # record/field is an interrupted measurement and cannot establish the guard.
    valid_gen = [r for r in gen if r["ok"] and not r.get("halt")]
    missing_overflow = 0
    missing_contrast = 0
    refused_contrast = 0
    for r in valid_gen:
        m = measured.get((r["brief"], r["rep"], r["arm"]))
        contrast = m.get("contrast") if m else None
        if not m or not isinstance(m.get("overflow"), bool):
            missing_overflow += 1
        if not isinstance(contrast, dict) or not isinstance(contrast.get("measured"), bool):
            missing_contrast += 1
        elif not contrast["measured"]:
            refused_contrast += 1
        elif type(contrast.get("low")) is not int or contrast["low"] < 0:
            missing_contrast += 1
    out["measurement_coverage"] = {
        "valid_artboards": len(valid_gen), "overflow_missing": missing_overflow,
        "contrast_missing": missing_contrast, "contrast_refused": refused_contrast,
    }
    out["gen_valid1"] = paired_binary(_pairs(gen, "valid1"))
    both = defaultdict(dict)
    for r in gen:
        both[(r["brief"], r["rep"])][r["arm"]] = r
    for name, flag, gate in (("contrast", "contrast_bad", "contrast_measured"), ("overflow", "overflow", "overflow_measured")):
        pairs = [(v["A"][flag], v["B"][flag], b) for (b, _), v in both.items()
                 if "A" in v and "B" in v and not v["A"].get("halt") and not v["B"].get("halt")
                 and v["A"]["ok"] and v["B"]["ok"] and v["A"][gate] and v["B"][gate]]
        out[f"gen_{name}"] = paired_binary(pairs)
    out["gen_halts"] = sum(1 for r in gen if r.get("halt"))
    for r in refine:
        shape = r["shape"]
        r["success"] = bool(r["ok"] and r["calls"] == 1 and shape["edits"] >= 1 and shape["whole"] == 0)
    out["refine_success"] = paired_binary(_pairs(refine, "success"))
    out["refine_halts"] = sum(1 for r in refine if r.get("halt"))
    tokens: dict[str, Any] = {}
    for label, pick in (("all", lambda r: True), ("3", lambda r: r["screens"] == 3),
                        ("4", lambda r: r["screens"] == 4), ("5", lambda r: r["screens"] == 5)):
        valid = _pairs([r for r in refine if pick(r)], "prompt")
        sums = {"A": sum(a for a, _, _ in valid), "B": sum(b for _, b, _ in valid)}
        per: dict[str, dict[str, float]] = defaultdict(lambda: {"A": 0.0, "B": 0.0})
        for a, b, brief in valid:
            per[brief]["A"] += a
            per[brief]["B"] += b
        diffs = [v["A"] - v["B"] for v in per.values() if v["A"] and v["B"]]
        tokens[label] = {"A": sums["A"], "B": sums["B"],
                         "reduction": (1 - sums["B"] / sums["A"]) if sums["A"] else None,
                         "per_design_diff_t": pr.mean_t_interval(diffs) if len(diffs) > 1 else None}
    out["tokens"] = tokens
    reads: dict[str, Any] = {}
    for how in ("named", "ordinal"):
        pairs = _pairs([r for r in refine if r["how"] == how], "read_rounds")
        reads[how] = {"n": len(pairs), "read_rounds": sum(1 for _, b, _ in pairs if b > 0)}
    out["read_share_note"] = reads
    shapes = _pairs(refine, "shape")
    out["collateral"] = {"A": sum(1 for a, _, _ in shapes if a["collateral"] > 0),
                         "B": sum(1 for _, b, _ in shapes if b["collateral"] > 0)}
    out["strata_success"] = {
        how: paired_binary(_pairs([r for r in refine if r["how"] == how], "success")) for how in ("named", "ordinal")}
    from bench.design_refine.briefs import BRIEFS

    brief_index = {brief.id: index for index, brief in enumerate(BRIEFS)}
    out["screen_success"] = {
        str(n): paired_binary(_pairs([r for r in refine if r["screens"] == n], "success"))
        for n in (3, 4, 5)
    }
    out["order_success"] = {
        arm: paired_binary(_pairs([r for r in refine if r["brief"] in brief_index
                                  and ("A" if (brief_index[r["brief"]] + r["rep"]) % 2 == 0 else "B") == arm],
                                 "success"))
        for arm in ("A", "B")
    }
    out["corpus"] = {"designs": len(corpus), "retries": sum(c["tries"] for c in corpus),
                     "failed": len(_load(results / key / "corpus_failed.jsonl"))}
    rows = gen + refine
    out["usd_conservative"] = sum(r["usd"] for r in rows) + sum(c["usd"] for c in corpus)
    out["usd_live"] = sum(r["usd_live"] for r in rows) + sum(c["usd_live"] for c in corpus)
    return out


def gates(rep: dict[str, Any]) -> dict[str, Any]:
    g1 = rep["refine_success"]["diff_pp"]
    t = rep["tokens"]
    g3v = rep["gen_valid1"]["diff_pp"]
    coverage = rep["measurement_coverage"]
    return {
        "gate1": g1 is not None and g1 >= PAIR_GATE_PP,
        "gate2": all(t[k]["reduction"] is not None and t[k]["reduction"] >= TOKEN_GATE for k in ("all", "3")),
        "gate3": (coverage["valid_artboards"] > 0 and coverage["overflow_missing"] == 0
                  and coverage["contrast_missing"] == 0
                  and g3v is not None and g3v >= -GUARD_PP
                  and rep["gen_contrast"]["diff_pp"] is not None
                  and rep["gen_contrast"]["diff_pp"] <= GUARD_PP
                  and rep["gen_overflow"]["diff_pp"] is not None
                  and rep["gen_overflow"]["diff_pp"] <= GUARD_PP),
    }


def print_report() -> None:
    from run import RESULTS, spent

    reports = [model_report(k, RESULTS) for k in ("ds", "glm") if (RESULTS / k).exists()]
    for rep in reports:
        rep["gates"] = gates(rep)
    print(json.dumps(reports, indent=1, default=str))
    both = all(all(r["gates"].values()) for r in reports) and len(reports) == 2
    print("VERDICT: adopt" if both else "VERDICT: not adopted (a gate failed or a model is missing)")
    print("ledger US$", spent())
