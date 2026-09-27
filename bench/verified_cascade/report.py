"""The registered metrics of the verified-cascade bench (PREREGISTRATION.md §7–§8), from a run directory.

    python bench/verified_cascade/report.py DIR            # prints the report, writes DIR/report.json

Offline. Every arm is replayed over the logged calls (``replay.py``). **Blind until S3 is complete**
(§9, S2): while ``gates.json`` has no complete S3 (no graded A with nothing pending), no wrong-answer
count and no draft label is computed or printed — only mechanics and cost.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.verified_cascade.harness import THRESHOLD  # noqa: E402
from bench.verified_cascade.replay import (  # noqa: E402
    GRADERS,
    RunData,
    is_wrong,
    load_vslice,
    outcomes,
    policy_cost,
    read_key,
    shipped_label,
)
from bench.verified_cascade.stats import (  # noqa: E402
    auroc,
    bootstrap_ratio,
    brier,
    clustered_bootstrap_diff,
    cohen_kappa,
    ece,
    ece_floor,
    holm,
    mcnemar_exact,
    newcombe_paired,
    percentile,
    wilson,
)

F1 = (("B", "A"), ("D", "A"))
F2 = (("L", "A"), ("B", "L"), ("D", "L"), ("C", "A"), ("B", "C"))
SECONDARY = ("B_decl", "D_decl", "B@0.5", "B@0.9", "D@0.5", "D@0.9", "oracle")
REPLICATE = (("B2", "A2"), ("D2", "A2"), ("L", "A2"))
REPS = 10_000  # bootstrap resamples (§7); the tests lower it


def unblinded(gates: dict[str, Any]) -> bool:
    s3 = gates.get("s3") or {}
    return "W" in s3 and not s3.get("pending")


def mechanics(rd: RunData, gates: dict[str, Any]) -> dict[str, Any]:
    rows = list(rd.log.rows.values())
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted({r["kind"] for r in rows}):
        sel = [r for r in rows if r["kind"] == kind]
        ok = [r for r in sel if r.get("status") == "ok"]
        secs = [float(r.get("seconds") or 0.0) for r in ok]
        by_kind[kind] = {
            "calls": len(sel), "ok": len(ok), "halts": sum(1 for r in sel if r.get("status") == "halt"),
            "rate_limited": sum(1 for r in sel if r.get("status") == "rate_limited"),
            "usd": round(sum(float(r.get("usd") or 0.0) for r in sel), 6),
            "latency_p50": round(percentile(secs, 0.5), 3) if secs else None,
            "latency_p95": round(percentile(secs, 0.95), 3) if secs else None,
            "empty_before_reask": sum(1 for r in sel if r.get("empty_before_reask")),
            "off_pin": sum(1 for r in ok if r.get("pin") and str(r.get("provider", "")).lower() != str(r["pin"]).lower()),
            "tokens_in": sum(int(r.get("prompt_tokens") or 0) for r in ok),
            "tokens_out": sum(int(r.get("completion_tokens") or 0) for r in ok),
            "cache_read": sum(int(r.get("cache_read_tokens") or 0) for r in ok),
        }
    builds = Counter(r.get("resolved_model") for r in rows if r.get("verifier") and r.get("status") == "ok")
    return {"spent_usd": round(rd.log.spent, 4), "by_kind": by_kind, "verifier_builds": dict(builds),
            "fuse_reason": (gates.get("s0") or {}).get("fuse_reason"), "gates": {k: v.get("passed") for k, v in gates.items()}}


def arm_table(rd: RunData) -> dict[str, list[dict[str, Any]]]:
    """Per item, per arm: shipped label, wrong, cost, escalated, hand-off."""
    table: dict[str, list[dict[str, Any]]] = {}
    for item in rd.items:
        for arm, (out, info) in outcomes(rd, item).items():
            label = shipped_label(rd, item, out)
            table.setdefault(arm, []).append({
                "item_id": item["item_id"], "question_id": item["question_id"], "family": item["family"],
                "lang": item["lang"], "shipped": out.shipped, "label": label, "wrong": is_wrong(label),
                "missing": label is None, "cost": policy_cost(rd, item, out), "escalated": out.escalated,
                "handoff": out.shipped == "handoff", "info": info,
            })
    return table


def paired(table: dict[str, list[dict[str, Any]]], x: str, a: str) -> dict[str, Any]:
    rows = [(rx, ra) for rx, ra in zip(table[x], table[a], strict=True) if not rx["missing"] and not ra["missing"]]
    wx = [rx["wrong"] for rx, _ in rows]
    wa = [ra["wrong"] for _, ra in rows]
    b = sum(1 for p, q in zip(wx, wa, strict=True) if q and not p)
    c = sum(1 for p, q in zip(wx, wa, strict=True) if p and not q)
    diff, lo, hi = newcombe_paired(wx, wa)
    cl = clustered_bootstrap_diff([float(v) for v in wx], [float(v) for v in wa], [rx["question_id"] for rx, _ in rows], reps=REPS)
    ratio = bootstrap_ratio([rx["cost"] for rx, _ in rows], [ra["cost"] for _, ra in rows], reps=REPS)
    return {"n": len(rows), "wrong_x": sum(wx), "wrong_a": sum(wa), "b": b, "c": c, "p": mcnemar_exact(b, c),
            "diff": diff, "newcombe": [lo, hi], "cluster_ci": [cl[1], cl[2]], "cost_ratio": list(ratio)}


def helpfulness(table: dict[str, list[dict[str, Any]]], x: str, a: str) -> dict[str, Any]:
    rows = [(rx, ra) for rx, ra in zip(table[x], table[a], strict=True)
            if rx["family"] == "ANS" and not rx["missing"] and not ra["missing"]]
    cx = [rx["label"] == "correct" for rx, _ in rows]
    ca = [ra["label"] == "correct" for _, ra in rows]
    handoffs = sum(1 for rx, _ in rows if rx["handoff"])
    unnecessary = sum(1 for rx, ra in rows if rx["handoff"] and ra["label"] == "correct")
    d, lo, hi = newcombe_paired(cx, ca)
    return {"n_ans": len(rows), "handoffs": handoffs, "handoff_rate": handoffs / len(rows) if rows else None,
            "handoff_wilson_upper": wilson(handoffs, len(rows))[1], "unnecessary_handoffs": unnecessary,
            "correct_x": sum(cx), "correct_a": sum(ca), "correct_diff": d, "correct_newcombe": [lo, hi]}


def per_arm(table: dict[str, list[dict[str, Any]]], arm: str) -> dict[str, Any]:
    rows = table[arm]
    done = [r for r in rows if not r["missing"]]
    out: dict[str, Any] = {"n": len(done), "missing": len(rows) - len(done), "wrong": sum(r["wrong"] for r in done),
                           "escalated": sum(r["escalated"] for r in done), "handoffs": sum(r["handoff"] for r in done),
                           "mean_cost": sum(r["cost"] for r in done) / len(done) if done else None}
    correct = sum(1 for r in done if r["label"] == "correct")
    out["cost_per_correct"] = sum(r["cost"] for r in done) / correct if correct else None
    for key in ("family", "lang"):
        groups = Counter(r[key] for r in done)
        out[f"wrong_by_{key}"] = {g: f"{sum(r['wrong'] for r in done if r[key] == g)}/{n}" for g, n in sorted(groups.items())}
    return out


def verdict(x: str, prim: dict[str, Any], help_: dict[str, Any], gates: dict[str, Any], mech: dict[str, Any], halts: float) -> dict[str, Any]:
    """§8's frozen rule for X in {B, D}."""
    inst = ((gates.get("s1") or {}).get("instrument") or {}).get("jev" if x == "B" else "local", {})
    ratio = prim["cost_ratio"][0]
    c1 = prim["diff"] < 0 and prim["p_holm"] < 0.05 and prim["cluster_ci"][1] < 0
    c2 = ratio == ratio and ratio <= 5.0
    c3 = help_["handoff_rate"] is not None and help_["handoff_rate"] <= 0.15
    calls = sum(v["ok"] for v in mech["by_kind"].values())
    off = sum(v["off_pin"] for v in mech["by_kind"].values())
    c4 = bool(inst.get("passed")) and halts <= 0.05 and (calls == 0 or off / calls <= 0.01)
    opt_in = c1 and c2 and c3 and c4
    # `is not None`, as c3 reads it: `or 1.0` turned the best possible rate, 0.0, into 1.0 and failed
    # the default on it (found reading the first real report, 2026-09-27; Amendment 3).
    rate = help_["handoff_rate"]
    default = (opt_in and ratio <= 3.0 and rate is not None and rate <= 0.05
               and help_["correct_newcombe"][0] >= -0.05)
    return {"fewer_wrong": c1, "cost_ok": c2, "handoffs_ok": c3, "eligible": c4, "opt_in": opt_in, "default": default}


def draft_truth(family: str, label: str) -> str:
    """§7: correct/incomplete -> supported, wrong -> unsupported, declined -> declined. On an NCR or
    NCP item a *correct* draft is a decline by definition (§5.1), so its truth is ``declined``."""
    if label == "wrong":
        return "unsupported"
    if label == "declined" or (family != "ANS" and label == "correct"):
        return "declined"
    return "supported"


def verifier_block(rd: RunData, verifier: str) -> dict[str, Any]:
    vs = load_vslice()
    per_kind: dict[str, Counter[str]] = {}
    for v in vs:
        row = rd.log.get(read_key(verifier, v["vid"]))
        if row and row.get("status") == "ok":
            per_kind.setdefault(f"{v['kind']}:{v['lang']}", Counter())[str(row.get("choice"))] += 1
    accepted_p: list[float] = []
    correct_p: list[float] = []
    wrong_p: list[float] = []
    conf: Counter[str] = Counter()
    argmax_vs_threshold = 0
    for item in rd.items:
        for draw in ("d1", "f1"):
            label, _ = rd.label(item["item_id"], draw)
            r = rd.log.get(read_key(verifier, item["item_id"], draw))
            if label is None or not r or r.get("status") != "ok" or r.get("p") is None:
                continue
            truth = draft_truth(item["family"], label)
            conf[f"{truth}->{r.get('choice')}"] += 1
            p = float(r["p"])
            if r.get("choice") == "supported" and p >= THRESHOLD:
                accepted_p.append(p)
            if (r.get("choice") == "supported") != (p >= THRESHOLD):
                argmax_vs_threshold += 1
            if truth == "supported":
                correct_p.append(p)
            elif label == "wrong":
                wrong_p.append(p)
    n_all = sum(conf.values())
    acc_rate = len(accepted_p) / n_all if n_all else None
    rej_wrong = sum(1 for p in wrong_p if p < THRESHOLD) / len(wrong_p) if wrong_p else None
    probs = correct_p + wrong_p
    outs = [True] * len(correct_p) + [False] * len(wrong_p)
    return {
        "v_slice_choices": {k: dict(c) for k, c in sorted(per_kind.items())},
        "draft_confusion": dict(conf), "acceptance_rate": acc_rate, "rejection_on_wrong": rej_wrong,
        "not_verifying": bool(acc_rate is not None and rej_wrong is not None and acc_rate > 0.90 and rej_wrong < 0.50),
        "auroc_raw": auroc(correct_p, wrong_p), "brier_raw": brier(probs, outs), "ece_raw": ece(probs, outs),
        "ece_floor": ece_floor(probs), "lowest_p_accepted": min(accepted_p) if accepted_p else None,
        "argmax_vs_threshold_disagreements": argmax_vs_threshold,
    }


def grader_block(rd: RunData) -> dict[str, Any]:
    a: list[str] = []
    b: list[str] = []
    adjudicated = graded = 0
    for item in rd.items:
        for draw in ("d1", "d2", "d3", "f1", "f2"):
            votes = rd.votes(item["item_id"], draw)
            if all(votes.values()):
                a.append(str(votes["g1"]))
                b.append(str(votes["g2"]))
            label, src = rd.label(item["item_id"], draw)
            if label is not None:
                graded += 1
                adjudicated += src == "adjudicated"
    floors = {}
    for prefix in ("regrade", "paraphrase"):
        same = n = 0
        for item in rd.items:
            for g in GRADERS:
                base = rd.log.get(f"grade|{g}|{item['item_id']}|d1")
                rep = rd.log.get(f"{prefix}|{g}|{item['item_id']}|d1")
                if base and rep and base.get("status") == rep.get("status") == "ok":
                    n += 1
                    same += base.get("label") == rep.get("label")
        floors[prefix] = {"n": n, "agreement": same / n if n else None}
    return {"kappa_g1_g2": cohen_kappa(a, b), "n_both": len(a), "graded": graded, "adjudicated": adjudicated,
            "adjudication_rate": adjudicated / graded if graded else None, "floors": floors,
            "commitment": dict(Counter(a))}


def build(out: Path) -> dict[str, Any]:
    rd = RunData(out)
    gates = json.loads((out / "gates.json").read_text(encoding="utf-8")) if (out / "gates.json").exists() else {}
    mech = mechanics(rd, gates)
    report: dict[str, Any] = {"mechanics": mech, "blind": not unblinded(gates)}
    if report["blind"]:
        return report
    table = arm_table(rd)
    report["arms"] = {arm: per_arm(table, arm) for arm in table}
    prim = {f"{x}-{a}": paired(table, x, a) for x, a in F1 + F2 + REPLICATE}
    for fam in (F1, F2):
        adj = holm({f"{x}-{a}": prim[f"{x}-{a}"]["p"] for x, a in fam})
        for k, v in adj.items():
            prim[k]["p_holm"] = v
    for x, a in REPLICATE:
        prim[f"{x}-{a}"]["p_holm"] = None
    report["paired"] = prim
    report["helpfulness"] = {x: helpfulness(table, x, "A") for x in ("B", "D", "L", "C", *SECONDARY)}
    total = len(rd.items)
    report["verdict"] = {
        x: verdict(x, prim[f"{x}-A"], report["helpfulness"][x], gates, mech, report["arms"][x]["missing"] / total if total else 1.0)
        for x in ("B", "D")
    }
    if report["verdict"]["B"]["opt_in"] and report["verdict"]["D"]["opt_in"]:
        bd = paired(table, "B", "D")
        level = "default" if report["verdict"]["B"]["default"] == report["verdict"]["D"]["default"] else "mixed"
        # §8: both qualify -> fewer wrong answers wins; B - D not significant -> D (US$ 0, offline).
        if level == "mixed":
            bd["preferred"] = "B" if report["verdict"]["B"]["default"] else "D"
        else:
            bd["preferred"] = "D" if bd["p"] >= 0.05 or bd["wrong_x"] >= bd["wrong_a"] else "B"
        report["b_vs_d"] = bd
    report["verifiers"] = {v: verifier_block(rd, v) for v in ("jev", "local")}
    report["graders"] = grader_block(rd)
    lex = Counter("majority" if r["info"].get("majority") else "no_majority" for r in table["L"] if r["info"])
    report["lexical"] = dict(lex)
    return report


def fmt(report: dict[str, Any]) -> str:
    m = report["mechanics"]
    lines = [f"spent US$ {m['spent_usd']}; gates {m['gates']}; fuse_reason {m['fuse_reason']}; builds {m['verifier_builds']}"]
    for kind, k in m["by_kind"].items():
        lines.append(f"  {kind:<11} calls {k['calls']:>5} ok {k['ok']:>5} halts {k['halts']} rl {k['rate_limited']} "
                     f"US$ {k['usd']:.4f} p50 {k['latency_p50']} p95 {k['latency_p95']} empty {k['empty_before_reask']} off-pin {k['off_pin']}")
    if report["blind"]:
        lines.append("BLIND: S3 is not complete; no label or wrong-answer count is computed (§9, S2).")
        return "\n".join(lines)
    lines.append("\narm      n  wrong  esc  handoff  mean US$   by family / language")
    for arm, a in report["arms"].items():
        lines.append(f"{arm:<7}{a['n']:>4}{a['wrong']:>7}{a['escalated']:>5}{a['handoffs']:>9}  {a['mean_cost'] or 0:.5f}   "
                     f"{a['wrong_by_family']} {a['wrong_by_lang']}")
    lines.append("\npaired     n   wrong X/A   b   c   p       p_holm  diff    Newcombe          cluster CI        cost X/A")
    for k, p in report["paired"].items():
        ph = "-" if p.get("p_holm") is None else f"{p['p_holm']:.4f}"
        lines.append(f"{k:<8}{p['n']:>4}  {p['wrong_x']:>4}/{p['wrong_a']:<4} {p['b']:>3} {p['c']:>3}  {p['p']:.4f}  {ph:<7} "
                     f"{p['diff']:+.4f} [{p['newcombe'][0]:+.3f},{p['newcombe'][1]:+.3f}] "
                     f"[{p['cluster_ci'][0]:+.3f},{p['cluster_ci'][1]:+.3f}] {p['cost_ratio'][0]:.2f}")
    lines.append("\nANS helpfulness vs A: arm handoffs (rate, Wilson hi) unnecessary  correct X/A  Newcombe")
    for x, h in report["helpfulness"].items():
        lines.append(f"  {x:<7} {h['handoffs']:>3} ({(h['handoff_rate'] or 0):.3f}, {h['handoff_wilson_upper']:.3f}) {h['unnecessary_handoffs']:>3}  "
                     f"{h['correct_x']}/{h['correct_a']}  [{h['correct_newcombe'][0]:+.3f},{h['correct_newcombe'][1]:+.3f}]")
    lines.append(f"\nverdict (§8): {report['verdict']}")
    for v, b in report["verifiers"].items():
        lines.append(f"verifier {v}: accept {b['acceptance_rate']} reject-on-wrong {b['rejection_on_wrong']} not_verifying {b['not_verifying']} "
                     f"AUROC {b['auroc_raw']:.3f} Brier {b['brier_raw']:.3f} ECE {b['ece_raw']:.3f} (floor {b['ece_floor']:.3f}) "
                     f"lowest accepted p {b['lowest_p_accepted']}")
    g = report["graders"]
    lines.append(f"graders: kappa {g['kappa_g1_g2']:.3f} on {g['n_both']}; adjudicated {g['adjudicated']}/{g['graded']}; floors {g['floors']}")
    lines.append(f"lexical L: {report['lexical']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    args = ap.parse_args(argv)
    report = build(args.out)
    (args.out / "report.json").write_text(json.dumps(report, indent=1, default=str, ensure_ascii=False) + "\n", encoding="utf-8")
    print(fmt(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
