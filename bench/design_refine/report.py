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
        r["contrast_bad"] = bool(m and m["contrast"]["measured"] and m["contrast"]["low"] > 0)
        r["contrast_measured"] = bool(m and m["contrast"]["measured"])
        r["overflow"] = bool(m and m["overflow"])
    out["gen_valid1"] = paired_binary(_pairs(gen, "valid1"))
    both = defaultdict(dict)
    for r in gen:
        both[(r["brief"], r["rep"])][r["arm"]] = r
    for name, flag, gate in (("contrast", "contrast_bad", "contrast_measured"), ("overflow", "overflow", "ok")):
        pairs = [(v["A"][flag], v["B"][flag], b) for (b, _), v in both.items()
                 if "A" in v and "B" in v and v["A"][gate] and v["B"][gate]]
        out[f"gen_{name}"] = paired_binary(pairs)
    out["gen_halts"] = sum(1 for r in gen if r.get("halt"))
    for r in refine:
        r["read"] = r["shape"]  # alias kept for the dump
    out["refine_success"] = paired_binary(_pairs(refine, "success"))
    out["refine_halts"] = sum(1 for r in refine if r.get("halt"))
    tokens: dict[str, Any] = {}
    for label, pick in (("all", lambda r: True), ("3", lambda r: r["screens"] == 3),
                        ("4", lambda r: r["screens"] == 4), ("5", lambda r: r["screens"] == 5)):
        rows = [r for r in refine if pick(r) and not r.get("halt")]
        sums = {arm: sum(r["prompt"] for r in rows if r["arm"] == arm) for arm in "AB"}
        per: dict[str, dict[str, float]] = defaultdict(lambda: {"A": 0.0, "B": 0.0})
        for r in rows:
            per[r["brief"]][r["arm"]] += r["prompt"]
        diffs = [v["A"] - v["B"] for v in per.values() if v["A"] and v["B"]]
        tokens[label] = {"A": sums["A"], "B": sums["B"],
                         "reduction": (1 - sums["B"] / sums["A"]) if sums["A"] else None,
                         "per_design_diff_t": pr.mean_t_interval(diffs) if len(diffs) > 1 else None}
    out["tokens"] = tokens
    reads: dict[str, Any] = {}
    for how in ("named", "ordinal"):
        rows = [r for r in refine if r["arm"] == "B" and r["how"] == how and not r.get("halt")]
        reads[how] = {"n": len(rows), "read_rounds": sum(1 for r in rows if r["calls"] > 1 and r["prompt"])}
    out["read_share_note"] = reads
    out["collateral"] = {arm: sum(1 for r in refine if r["arm"] == arm and r["shape"]["collateral"] > 0) for arm in "AB"}
    out["strata_success"] = {
        how: paired_binary(_pairs([r for r in refine if r["how"] == how], "success")) for how in ("named", "ordinal")}
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
    return {
        "gate1": g1 is not None and g1 >= PAIR_GATE_PP,
        "gate2": all(t[k]["reduction"] is not None and t[k]["reduction"] >= TOKEN_GATE for k in ("all", "3")),
        "gate3": (g3v is not None and g3v >= -GUARD_PP
                  and (rep["gen_contrast"]["diff_pp"] or 0) <= GUARD_PP
                  and (rep["gen_overflow"]["diff_pp"] or 0) <= GUARD_PP),
    }


def print_report() -> None:
    from run import RESULTS

    reports = [model_report(k, RESULTS) for k in ("ds", "glm") if (RESULTS / k).exists()]
    for rep in reports:
        rep["gates"] = gates(rep)
    print(json.dumps(reports, indent=1, default=str))
    both = all(all(r["gates"].values()) for r in reports) and len(reports) == 2
    print("VERDICT: adopt" if both else "VERDICT: not adopted (a gate failed or a model is missing)")
    print("ledger US$", json.load(open(RESULTS / "spend.json"))["usd"])
