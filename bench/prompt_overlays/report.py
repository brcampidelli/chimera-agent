"""The registered analysis of H4 and H5 (PREREGISTRATION.md, "Analysis"). Pure Python, no model calls.

Reads `results/main_solves.jsonl`, `results/pilot_solves.jsonl` and the official harness reports in
`results/grades/`, prints every registered number, and writes `results/summary.json`.
"""

from __future__ import annotations

import json
import random
import re
import statistics
from typing import Any

from run import GRADES, MAIN, PILOT, RESULTS, load_jsonl, solve_cost

from chimera.eval import proportions

MARGIN = 0.10  # H4 non-inferiority margin on the resolve rate (registered)
BOOT = 10_000
SEED = 25

#: A shell command that reaches the network (PROTOCOL §1: the wall this bench cannot enforce).
NET = re.compile(r"\b(curl|wget|pip3? install|pip3? download|uv pip|git (clone|fetch|pull)|https?://)", re.I)
#: ...and one that could fetch django's own source, the leak that would matter. Widened by Amendment 2
#: after the first pilot's `raw.githubusercontent.com/django/...` fetch, which the first version missed.
DJANGO_FETCH = re.compile(
    r"(github(usercontent)?\.com/django|repo:django|djangoproject\.com|pypi[^\s]*django"
    r"|pip3? (install|download)[^\n]*\bdjango\b|git (clone|fetch|ls-remote)[^\n]*django)", re.I)
#: A command that tries to step around the proxy wall of Amendment 2.
WALL_BYPASS = re.compile(r"(--noproxy|no_proxy=|unset [^\n]*proxy|env -u [^\n]*proxy|proxy=\"?\"?(\s|$))", re.I)


def grades(phase: str, arm: str) -> dict[str, set[str]] | None:
    hits = sorted(GRADES.glob(f"h45-{phase}-{arm}.*.json"))
    if not hits:
        return None
    rep = json.loads(hits[-1].read_text(encoding="utf-8"))
    return {k: set(rep.get(k, [])) for k in ("resolved_ids", "unresolved_ids", "error_ids", "empty_patch_ids",
                                              "completed_ids", "submitted_ids")}


def outcome(row: dict[str, Any], g: dict[str, set[str]] | None, *, timeout_is_halt: bool) -> bool | None:
    """True/False = graded resolved or not; None = a halt that leaves the pairing (PROTOCOL §2)."""
    iid = row["instance_id"]
    halted = row.get("halted")
    if halted and halted != "timeout":
        return None
    if halted == "timeout" and timeout_is_halt:
        return None
    if not (row.get("patch") or "").strip():
        return False  # an empty patch is the agent's own failure, graded or not
    if g is None or iid in g["error_ids"]:
        return None  # the harness could not grade it: an infrastructure halt
    return iid in g["resolved_ids"]


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    return proportions.wilson(k, n, z)


def newcombe_paired(e: int, f: int, g: int, h: int) -> tuple[float, float, float]:
    """Newcombe (1998) method 10 for p_new - p_ref on paired data.

    e: both resolved, f: new only, g: ref only, h: neither. Returns (delta, lower, upper)."""
    n = e + f + g + h
    low, high = proportions.newcombe_paired(e, g, f, h)  # the new arm is the treatment
    return (f - g) / n, low, high


def mcnemar_exact(b: int, c: int) -> float:
    return proportions.mcnemar_exact(b, c)


def sign_test(pos: int, neg: int) -> float:
    return mcnemar_exact(pos, neg)


def tokens(row: dict[str, Any]) -> int:
    return sum((c.get("prompt") or 0) + (c.get("completion") or 0) for c in row.get("calls") or [])


def boot_ratio(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Ratio of means sum(ys)/sum(xs) with a paired percentile bootstrap over items."""
    rng = random.Random(SEED)
    n = len(xs)
    point = sum(ys) / sum(xs) if sum(xs) else float("nan")
    draws = []
    for _ in range(BOOT):
        idx = [rng.randrange(n) for _ in range(n)]
        sx = sum(xs[i] for i in idx)
        if sx:
            draws.append(sum(ys[i] for i in idx) / sx)
    draws.sort()
    return point, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]


def cost_per_success_ratio(ref: list[tuple[float, bool]], new: list[tuple[float, bool]]) -> tuple[float, float, float]:
    """(cost/successes of new) / (cost/successes of ref), paired bootstrap over items."""
    rng = random.Random(SEED + 1)
    n = len(ref)

    def ratio(idx: list[int]) -> float | None:
        sr = sum(ref[i][1] for i in idx)
        sn = sum(new[i][1] for i in idx)
        if not sr or not sn:
            return None
        return (sum(new[i][0] for i in idx) / sn) / (sum(ref[i][0] for i in idx) / sr)

    point = ratio(list(range(n)))
    draws = sorted(r for r in (ratio([rng.randrange(n) for _ in range(n)]) for _ in range(BOOT)) if r is not None)
    return (point or float("nan"), draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1])


def arm_table(rows: list[dict[str, Any]], g: dict[str, set[str]] | None) -> dict[str, Any]:
    done = [r for r in rows if not (r.get("halted") and r["halted"] != "timeout")]
    res = [outcome(r, g, timeout_is_halt=True) for r in rows]
    graded = [x for x in res if x is not None]
    steps = [r["steps"] for r in done if "steps" in r]
    return {
        "solves": len(rows),
        "resolved": sum(graded), "graded": len(graded),
        "rate": round(sum(graded) / len(graded), 4) if graded else None,
        "halted_error": sum(1 for r in rows if r.get("halted") and r["halted"] != "timeout"),
        "timeouts": sum(1 for r in rows if r.get("halted") == "timeout"),
        "harness_errors": sum(1 for r in rows if g and r["instance_id"] in g["error_ids"]),
        "empty_patch": sum(1 for r in done if not (r.get("patch") or "").strip()),
        "max_steps": sum(1 for r in done if r.get("stopped") == "max_steps"),
        "tool_loop": sum(1 for r in done if r.get("stopped") == "tool_loop"),
        "mean_steps": round(statistics.mean(steps), 2) if steps else None,
        "mean_tokens": round(statistics.mean(tokens(r) for r in done)) if done else None,
        "mean_usd": round(statistics.mean(solve_cost(r) for r in done), 5) if done else None,
        "usd_total": round(sum(solve_cost(r) for r in rows), 4),
        "cache_read_share": round(
            sum(c.get("cache_read") or 0 for r in rows for c in r.get("calls") or [])
            / max(1, sum(c.get("prompt") or 0 for r in rows for c in r.get("calls") or [])), 4),
        "providers": sorted({c.get("provider") or "?" for r in rows for c in r.get("calls") or []}),
        "net_commands": sum(1 for r in rows if any(NET.search(s) for s in r.get("shell") or [])),
        "django_fetch": sum(1 for r in rows if any(DJANGO_FETCH.search(s) for s in r.get("shell") or [])),
        "wall_bypass_tries": sum(1 for r in rows if any(WALL_BYPASS.search(s) for s in r.get("shell") or [])),
    }


def compare(ref: str, new: str, by: dict[str, dict[str, dict[str, Any]]], g: dict[str, Any],
            *, timeout_is_halt: bool = True) -> dict[str, Any]:
    e = f = gg = h = 0
    tok_pos = tok_neg = 0
    tref: list[float] = []
    tnew: list[float] = []
    cref: list[tuple[float, bool]] = []
    cnew: list[tuple[float, bool]] = []
    for arms in by.values():
        if ref not in arms or new not in arms:
            continue
        o_ref = outcome(arms[ref], g[ref], timeout_is_halt=timeout_is_halt)
        o_new = outcome(arms[new], g[new], timeout_is_halt=timeout_is_halt)
        if o_ref is None or o_new is None:
            continue
        e += o_ref and o_new
        f += o_new and not o_ref
        gg += o_ref and not o_new
        h += not o_ref and not o_new
        a_tok, b_tok = tokens(arms[ref]), tokens(arms[new])
        tok_pos += b_tok < a_tok
        tok_neg += b_tok > a_tok
        tref.append(a_tok)
        tnew.append(b_tok)
        cref.append((solve_cost(arms[ref]), o_ref))
        cnew.append((solve_cost(arms[new]), o_new))
    n = e + f + gg + h
    if n == 0:
        return {"n": 0}
    d, lo, hi = newcombe_paired(e, f, gg, h)
    tr = boot_ratio(tref, tnew)
    cr = boot_ratio([c for c, _ in cref], [c for c, _ in cnew])
    cps = cost_per_success_ratio(cref, cnew)
    from chimera.eval.paired import PairedResult

    closed_phase_ci = PairedResult(ref, new, e, gg, f, h).diff_ci
    return {
        "n": n, "both": e, "new_only": f, "ref_only": gg, "neither": h,
        "rate_ref": round((e + gg) / n, 4), "rate_new": round((e + f) / n, 4),
        "delta": round(d, 4), "newcombe95": [round(lo, 4), round(hi, 4)],
        "paired_py_ci95": [round(x, 4) for x in closed_phase_ci],
        "mcnemar_p": round(mcnemar_exact(gg, f), 5), "p_d": round((f + gg) / n, 4),
        "tokens_new_fewer": tok_pos, "tokens_new_more": tok_neg,
        "tokens_sign_p": round(sign_test(tok_pos, tok_neg), 5),
        "tokens_ratio": [round(x, 4) for x in tr],
        "usd_ratio": [round(x, 4) for x in cr],
        "usd_per_success_ratio": [round(x, 4) for x in cps],
    }


def report() -> None:
    main = load_jsonl(MAIN)
    pilot = load_jsonl(PILOT)
    g = {arm: grades("main", arm) for arm in ("A", "B", "C")}
    by: dict[str, dict[str, dict[str, Any]]] = {}
    for r in main:
        by.setdefault(r["instance_id"], {})[r["arm"]] = r
    out: dict[str, Any] = {"arms": {}, "checks": {}}
    for arm in ("A", "B", "C"):
        rows = [r for r in main if r["arm"] == arm]
        if rows:
            out["arms"][arm] = arm_table(rows, g[arm])
    # H4: B against A. H5: C against A. Timeouts are halts (primary) or graded (sensitivity).
    out["H4"] = compare("A", "B", by, g)
    out["H4_timeouts_graded"] = compare("A", "B", by, g, timeout_is_halt=False)
    out["H5"] = compare("A", "C", by, g)
    out["H5_timeouts_graded"] = compare("A", "C", by, g, timeout_is_halt=False)
    # Amendment 2: items where any arm ran a command that could fetch django's source leave a
    # sensitivity reading (reported beside the primary, never instead of it).
    leaks = sorted({r["instance_id"] for r in main if any(DJANGO_FETCH.search(s) for s in r.get("shell") or [])})
    out["leak_items"] = leaks
    if leaks:
        clean = {iid: arms for iid, arms in by.items() if iid not in leaks}
        out["H4_without_leak_items"] = compare("A", "B", clean, g)
        out["H5_without_leak_items"] = compare("A", "C", clean, g)
    h4 = out["H4"]
    if h4.get("n"):
        lo = h4["newcombe95"][0]
        cheaper = h4["tokens_sign_p"] < 0.05 and h4["tokens_new_fewer"] > h4["tokens_new_more"]
        out["H4_decision"] = {
            "non_inferior": lo >= -MARGIN, "superior": lo > 0, "inferior": h4["newcombe95"][1] < 0,
            "cheaper_tokens": cheaper,
            "cost_per_success_within_20pct": h4["usd_per_success_ratio"][0] <= 1.20,
        }
    h5 = out["H5"]
    if h5.get("n"):
        out["H5_decision"] = {"better": h5["delta"] > 0 and h5["mcnemar_p"] < 0.05,
                              "worse": h5["delta"] < 0 and h5["mcnemar_p"] < 0.05,
                              "cost_per_success_within_20pct": h5["usd_per_success_ratio"][0] <= 1.20}
    # Floor: arm A on the pilot items, pilot run against main run.
    gp = grades("pilot", "A")
    dis = n_rep = 0
    for r in pilot:
        m = by.get(r["instance_id"], {}).get("A")
        if not m:
            continue
        a1, a2 = outcome(r, gp, timeout_is_halt=True), outcome(m, g["A"], timeout_is_halt=True)
        if a1 is None or a2 is None:
            continue
        n_rep += 1
        dis += a1 != a2
    out["floor_replica_A"] = {"disagree": dis, "n": n_rep}
    # Instrument checks: the composed system message differs only where the arm says.
    shas: dict[str, set[str]] = {}
    for r in main:
        if r.get("system_sha256"):
            shas.setdefault(r["arm"], set()).add(r["system_sha256"])
    out["checks"]["system_sha_count"] = {k: len(v) for k, v in shas.items()}
    out["checks"]["A_equals_C_system"] = bool(shas.get("A")) and shas.get("A") == shas.get("C")
    out["checks"]["starts_with_arm"] = all(r.get("system_starts_with_arm", True) for r in main)
    temps = {(r["arm"], c.get("temperature"), c.get("top_p")) for r in main for c in r.get("calls") or []}
    out["checks"]["sampling_sent"] = sorted(map(str, temps))
    out["pilot"] = arm_table(pilot, gp) if pilot else None
    # The first pilot, run before the network wall (Amendment 2): kept as a record, used for nothing.
    pilot0 = load_jsonl(RESULTS / "pilot0_solves.jsonl")
    g0 = sorted(GRADES.glob("pilot0.*.json"))
    if pilot0:
        rep0 = json.loads(g0[-1].read_text(encoding="utf-8")) if g0 else {}
        out["pilot0_before_wall"] = arm_table(pilot0, {k: set(rep0.get(k, [])) for k in (
            "resolved_ids", "unresolved_ids", "error_ids", "empty_patch_ids", "completed_ids",
            "submitted_ids")} if rep0 else None)
    out["usd_all"] = round(sum(solve_cost(r) + solve_cost({"calls": r.get("retry_calls") or []})
                               for r in main + pilot + pilot0), 4)
    (RESULTS / "summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8",
                                                   newline="\n")
    print(json.dumps(out, indent=2))
