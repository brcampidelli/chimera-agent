"""The registered analysis of the default-model bake-off (PREREGISTRATION.md). No model calls.

    python bench/default_model/bakeoff_report.py [pilot]

Reads the solves and the official harness reports in `results/grades/`, prints every registered
number and the decision under the frozen rule, and writes `results/summary.json` (or
`results/pilot_summary.json`). The interval, test and bootstrap code is bench/prompt_overlays'
`report.py`, imported, so both benches read their pairs the same way.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO, REPO / "bench" / "prompt_overlays", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bakeoff import MAIN, PILOT, RESULTS, SLICE, load_jsonl, solve_usd  # noqa: E402
from bakeoff_arms import ARMS, ORDER, cost  # noqa: E402
from report import (  # noqa: E402  — bench/prompt_overlays/report.py
    DJANGO_FETCH,
    NET,
    WALL_BYPASS,
    boot_ratio,
    cost_per_success_ratio,
    mcnemar_exact,
    newcombe_paired,
    outcome,
    wilson,
)

GRADES = RESULTS / "grades"
#: The registered non-inferiority margin on the resolve rate (candidate minus A).
MARGIN = 0.05
#: Reported beside the decision, never used by it: the margin bench/prompt_overlays could test.
WIDE_MARGIN = 0.10
ALPHA = 0.05


def grades(phase: str, arm: str) -> dict[str, set[str]] | None:
    hits = sorted(GRADES.glob(f"dflt-{phase}-{arm}.dflt_{phase}_{arm}.json"))
    if not hits:
        return None
    rep = json.loads(hits[-1].read_text(encoding="utf-8"))
    return {k: set(rep.get(k, [])) for k in ("resolved_ids", "unresolved_ids", "error_ids", "empty_patch_ids",
                                              "completed_ids", "submitted_ids")}


def _tokens(row: dict[str, Any]) -> int:
    return sum((c.get("prompt") or 0) + (c.get("completion") or 0) for c in row.get("calls") or [])


def _mean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 5) if xs else None


def arm_table(arm: str, rows: list[dict[str, Any]], g: dict[str, set[str]] | None) -> dict[str, Any]:
    done = [r for r in rows if not r.get("halted")]
    graded = [x for x in (outcome(r, g, timeout_is_halt=True) for r in rows) if x is not None]
    k, n = sum(graded), len(graded)
    calls = [c for r in rows for c in r.get("calls") or []]
    billed = [c.get("billed") for c in calls]
    usd = [solve_usd(r) for r in done]
    return {
        "model": ARMS[arm].model, "pinned": ARMS[arm].provider,
        "solves": len(rows), "resolved": k, "graded": n,
        "rate": round(k / n, 4) if n else None, "wilson95": [round(x, 4) for x in wilson(k, n)] if n else None,
        "halted_error": sum(1 for r in rows if r.get("halted") and r["halted"] != "timeout"),
        "timeouts": sum(1 for r in rows if r.get("halted") == "timeout"),
        "retried": sum(1 for r in rows if r.get("retried_after")),
        "harness_errors": sum(1 for r in rows if g and r["instance_id"] in g["error_ids"]),
        "empty_patch": sum(1 for r in done if not (r.get("patch") or "").strip()),
        "stopped": dict(Counter(r.get("stopped") for r in done)),
        # #619: two empty closing replies at max_steps / tool_loop, reported by the loop's note.
        "empty_close_note": sum(1 for r in done if r.get("answer_empty_note")),
        # #624's ending (not on this branch): a natural "final" with no text at all.
        "empty_natural_final": sum(1 for r in done if r.get("stopped") == "final" and r.get("answer_empty")),
        "empty_calls": sum(1 for c in calls if c.get("empty")),
        "calls": len(calls),
        "zero_tool_call_solves": sum(1 for r in done if not r.get("tool_calls")),
        "tool_errors": dict(sum((Counter(r.get("tool_errors") or {}) for r in done), Counter())),
        "dropped_tool_calls": sum(r.get("dropped_tool_calls") or 0 for r in rows),
        "mean_steps": _mean([r["steps"] for r in done if "steps" in r]),
        "mean_seconds": _mean([r.get("seconds") or 0 for r in done]),
        "mean_tokens": _mean([_tokens(r) for r in done]),
        "mean_prompt": _mean([sum(c.get("prompt") or 0 for c in r.get("calls") or []) for r in done]),
        "mean_completion": _mean([sum(c.get("completion") or 0 for c in r.get("calls") or []) for r in done]),
        "mean_reasoning": _mean([sum(c.get("reasoning") or 0 for c in r.get("calls") or []) for r in done]),
        "cache_read_share": round(sum(c.get("cache_read") or 0 for c in calls)
                                  / max(1, sum(c.get("prompt") or 0 for c in calls)), 4),
        "mean_usd": _mean(usd),
        "usd_total": round(sum(solve_usd(r) + solve_usd(r, calls_key="retry_calls") for r in rows), 4),
        "usd_computed_total": round(sum(cost(r.get("calls") or [], ARMS[arm]) for r in rows), 4),
        "usd_billed_total": round(sum(b for b in billed if isinstance(b, (int, float))), 4),
        "billed_reported_calls": sum(1 for b in billed if isinstance(b, (int, float))),
        "usd_per_resolved": round(sum(usd) / k, 5) if k else None,
        "providers": dict(Counter(c.get("provider") or "?" for c in calls)),
        "temperatures_sent": sorted({str(c.get("temperature")) for c in calls}),
        "net_commands": sum(1 for r in rows if any(NET.search(s) for s in r.get("shell") or [])),
        "django_fetch": sum(1 for r in rows if any(DJANGO_FETCH.search(s) for s in r.get("shell") or [])),
        "wall_bypass_tries": sum(1 for r in rows if any(WALL_BYPASS.search(s) for s in r.get("shell") or [])),
    }


def compare(ref: str, new: str, by: dict[str, dict[str, dict[str, Any]]], g: dict[str, Any],
            *, timeout_is_halt: bool = True, margin: float = MARGIN) -> dict[str, Any]:
    e = f = gg = h = 0
    tok_fewer = tok_more = 0
    tref: list[float] = []
    tnew: list[float] = []
    cref: list[tuple[float, bool]] = []
    cnew: list[tuple[float, bool]] = []
    for arms in by.values():
        if ref not in arms or new not in arms:
            continue
        o_ref = outcome(arms[ref], g.get(ref), timeout_is_halt=timeout_is_halt)
        o_new = outcome(arms[new], g.get(new), timeout_is_halt=timeout_is_halt)
        if o_ref is None or o_new is None:
            continue
        e += o_ref and o_new
        f += o_new and not o_ref
        gg += o_ref and not o_new
        h += not o_ref and not o_new
        a_tok, b_tok = _tokens(arms[ref]), _tokens(arms[new])
        tok_fewer += b_tok < a_tok
        tok_more += b_tok > a_tok
        tref.append(a_tok)
        tnew.append(b_tok)
        cref.append((solve_usd(arms[ref]), o_ref))
        cnew.append((solve_usd(arms[new]), o_new))
    n = e + f + gg + h
    if n == 0:
        return {"n": 0}
    d, lo, hi = newcombe_paired(e, f, gg, h)
    cps = cost_per_success_ratio(cref, cnew) if (e + gg) and (e + f) else (float("nan"),) * 3
    p = mcnemar_exact(gg, f)
    return {
        "n": n, "both": e, "new_only": f, "ref_only": gg, "neither": h,
        "rate_ref": round((e + gg) / n, 4), "rate_new": round((e + f) / n, 4),
        "delta": round(d, 4), "newcombe95": [round(lo, 4), round(hi, 4)],
        "mcnemar_p": round(p, 5), "p_d": round((f + gg) / n, 4),
        "tokens_new_fewer": tok_fewer, "tokens_new_more": tok_more,
        "tokens_ratio": [round(x, 4) for x in boot_ratio(tref, tnew)],
        "usd_ratio": [round(x, 4) for x in boot_ratio([c for c, _ in cref], [c for c, _ in cnew])],
        "usd_per_resolved_ratio": [round(x, 4) for x in cps],
        "non_inferior": lo >= -margin,
        "cheaper_per_resolved": bool(cps[0] == cps[0] and cps[0] < 1.0),
        "superior": d > 0 and p < ALPHA,
        "_items": sorted(i for i, arms in by.items() if ref in arms and new in arms),
    }


def holm(pvals: dict[str, float]) -> dict[str, float]:
    order = sorted(pvals, key=pvals.get)  # type: ignore[arg-type]
    m, out, run = len(order), {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (m - i) * pvals[k]))
        out[k] = round(run, 5)
    return out


def decide(cmp: dict[str, dict[str, Any]], by: dict[str, dict[str, dict[str, Any]]],
           g: dict[str, Any]) -> dict[str, Any]:
    """The frozen rule: non-inferior at MARGIN, and cheaper per resolved or significantly better.
    Among qualifiers: highest resolve rate on the items every qualifier and A graded, then the
    lowest cost per resolved there. None qualifies: A stays."""
    qual = [k for k, c in cmp.items() if c.get("n") and c["non_inferior"]
            and (c["cheaper_per_resolved"] or c["superior"])]
    out: dict[str, Any] = {"qualifiers": qual, "winner": None}
    if not qual:
        out["decision"] = "no candidate qualifies; A stays the default"
        return out
    common = []
    for iid, arms in by.items():
        os_ = {a: outcome(arms[a], g.get(a), timeout_is_halt=True) for a in ["A", *qual] if a in arms}
        if len(os_) == len(qual) + 1 and all(o is not None for o in os_.values()):
            common.append(iid)
    rank = []
    for k in qual:
        res = [bool(outcome(by[i][k], g.get(k), timeout_is_halt=True)) for i in common]
        usd = sum(solve_usd(by[i][k]) for i in common)
        rate = sum(res) / len(res) if res else 0.0
        rank.append((-rate, (usd / sum(res)) if sum(res) else float("inf"), k))
    rank.sort()
    out.update({"common_items": len(common),
                "ranking": [{"arm": k, "rate": round(-r, 4), "usd_per_resolved": round(c, 5)} for r, c, k in rank],
                "winner": rank[0][2],
                "decision": f"{rank[0][2]} ({ARMS[rank[0][2]].model}) replaces A as the default"})
    return out


def report(phase: str = "main") -> dict[str, Any]:
    rows = load_jsonl(MAIN if phase == "main" else PILOT)
    arms_present = [a for a in ORDER if any(r["arm"] == a for r in rows)]
    g = {a: grades(phase, a) for a in arms_present}
    by: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["instance_id"], {})[r["arm"]] = r
    out: dict[str, Any] = {"phase": phase, "slice": len(load_jsonl(SLICE)), "items": len(by),
                           "arms": {a: arm_table(a, [r for r in rows if r["arm"] == a], g[a]) for a in arms_present}}
    pooled = [x for a in arms_present for x in (outcome(r, g[a], timeout_is_halt=True)
                                                 for r in rows if r["arm"] == a) if x is not None]
    out["pooled_rate"] = round(sum(pooled) / len(pooled), 4) if pooled else None
    cands = [a for a in arms_present if a != "A"]
    cmp = {a: compare("A", a, by, g) for a in cands}
    out["vs_A"] = {a: {k: v for k, v in c.items() if k != "_items"} for a, c in cmp.items()}
    if phase == "main":
        out["decision"] = decide(cmp, by, g)
        out["holm_mcnemar"] = holm({a: c["mcnemar_p"] for a, c in cmp.items() if c.get("n")})
        out["wide_margin_reading"] = {a: {"non_inferior_at_10pp": c["newcombe95"][0] >= -WIDE_MARGIN}
                                      for a, c in cmp.items() if c.get("n")}
        out["vs_A_timeouts_graded"] = {a: {k: v for k, v in compare("A", a, by, g, timeout_is_halt=False).items()
                                           if k != "_items"} for a in cands}
        leaks = sorted({r["instance_id"] for r in rows if any(DJANGO_FETCH.search(s) for s in r.get("shell") or [])})
        out["leak_items"] = leaks
        if leaks:
            clean = {i: arms for i, arms in by.items() if i not in leaks}
            out["vs_A_without_leak_items"] = {a: {k: v for k, v in compare("A", a, clean, g).items() if k != "_items"}
                                              for a in cands}
        # Replay floor: each arm's pilot solve against its main solve on the pilot items.
        pilot = load_jsonl(PILOT)
        floor = {}
        for a in arms_present:
            gp = grades("pilot", a)
            dis = n = 0
            for r in pilot:
                if r["arm"] != a:
                    continue
                m = by.get(r["instance_id"], {}).get(a)
                if not m:
                    continue
                x, y = outcome(r, gp, timeout_is_halt=True), outcome(m, g[a], timeout_is_halt=True)
                if x is None or y is None:
                    continue
                n += 1
                dis += x != y
            floor[a] = {"disagree": dis, "n": n}
        out["floor_replica"] = floor
    shas: dict[str, set[str]] = {}
    for r in rows:
        if r.get("system_sha256"):
            shas.setdefault(r["arm"], set()).add(r["system_sha256"])
    out["checks"] = {
        "system_sha_per_arm": {k: len(v) for k, v in shas.items()},
        "same_system_in_every_arm": len({s for v in shas.values() for s in v}) == 1 if shas else None,
        "starts_with_default": all(r.get("system_starts_with_default", True) for r in rows),
        "off_pin_calls": {a: sum(1 for r in rows if r["arm"] == a for c in r.get("calls") or []
                                 if (c.get("provider") or "").lower() != ARMS[a].provider.lower())
                          for a in arms_present},
    }
    out["usd_all"] = round(sum(solve_usd(r) + solve_usd(r, calls_key="retry_calls")
                               for path in (PILOT, MAIN) for r in load_jsonl(path)), 4)
    name = "summary.json" if phase == "main" else "pilot_summary.json"
    (RESULTS / name).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(json.dumps(report(sys.argv[1] if len(sys.argv) > 1 else "main"), indent=2))
