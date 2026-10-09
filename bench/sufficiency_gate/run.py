"""A sufficiency gate BEFORE generation, against the shipped verifier AFTER it (PREREGISTRATION.md).

    python bench/sufficiency_gate/run.py --collect --out bench/sufficiency_gate/results/run   # 400 gate reads + 100 floor reads, local
    python bench/sufficiency_gate/run.py --report  --out bench/sufficiency_gate/results/run   # offline: arms, rule, controls

The generations, the post-generation reads and every label are the frozen ones of
`bench/verified_cascade/results/run/` (2026-09-27): the gate sits in front of the generator and does
not change its prompt, so an item it lets through would have produced exactly the recorded ``d1``.
The only new calls are the gate's local ``qwen3:4b`` reads, logged append-only and resumable.
PREREGISTRATION.md is the rule; this file only produces the numbers it names.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.verified_cascade.common import RESULTS as VC_RESULTS  # noqa: E402
from bench.verified_cascade.common import sha  # noqa: E402
from bench.verified_cascade.harness import CallLog, Ledger, run_call  # noqa: E402
from bench.verified_cascade.replay import RunData, outcomes, policy_cost  # noqa: E402
from bench.verified_cascade.stats import auroc, mcnemar_exact, newcombe_paired, wilson  # noqa: E402

VC_RUN = VC_RESULTS / "run"
LOCAL = "qwen3:4b"
SEED = 26
FLOOR_ITEMS = 100
DRAWS = 1000
BOOT = 2000
#: The published replay this bench must reproduce before reading a gate number (§6, §2aa).
PUBLISHED = {"A": (33, 400), "D_decl": (21, 398)}

# --- the gate, word for word as PREREGISTRATION.md §2 fixes it --------------------------------------
DECISION = "bench.sufficiency_gate.sufficient_context"
KEY = "sufficient_context"
INSTRUCTIONS = "Do these excerpts contain enough information to answer the question?"
OPTIONS = ("sufficient", "insufficient")
CRITERIA = {
    "sufficient": "The excerpts state everything needed to answer the question.",
    "insufficient": "The excerpts do not state the answer to the question, or state only part of it.",
}
#: Shipped when the gate abstains: the product's decline (`chimera/fusion/verified.py::DECLINE_TEXT`).
DECLINE = "The sources provided don't cover this."
ANSWERED = ("correct", "incomplete")


def gate_question() -> Any:
    from chimera.decisions.contract import Choice

    return Choice(key=KEY, instructions=INSTRUCTIONS, options=OPTIONS, criteria=dict(CRITERIA),
                  event=("sufficient",), event_name="p_sufficient")


def gate_state(excerpts: Sequence[str], question: str) -> str:
    """The verifier's state minus the answer: excerpts in the item's order, then the question."""
    return json.dumps({"excerpts": list(excerpts), "question": question}, ensure_ascii=False)


class LocalGate:
    """``qwen3:4b`` through the product's own local backend, behind a Decider with no map (read raw)."""

    def __init__(self) -> None:
        from chimera.config import get_settings
        from chimera.decisions.contract import Decider
        from chimera.decisions.local import LocalLogprobBackend

        self.decider = Decider(LocalLogprobBackend(get_settings().ollama_base_url, LOCAL))
        self.question = gate_question()

    def read(self, state: str) -> dict[str, Any]:
        answer = self.decider.decide(DECISION, state, self.question)
        if answer.halt:
            raise RuntimeError(answer.halt)
        return {"choice": answer.choice, "p": answer.raw_p, "shares": answer.shares, "mass": answer.mass,
                "usd": 0.0, "resolved_model": answer.resolved_model, "seconds": round(answer.seconds, 3),
                "prompt_hash": answer.prompt_hash}


class FakeGate:
    """Offline and deterministic (tests only): p from a hash of the state."""

    def read(self, state: str) -> dict[str, Any]:
        p = int(sha(state)[:8], 16) / float(16**8)
        choice = "sufficient" if p >= 0.5 else "insufficient"
        return {"choice": choice, "p": p, "shares": {"sufficient": p, "insufficient": 1 - p}, "mass": 0.99,
                "usd": 0.0, "resolved_model": "fake", "seconds": 0.0, "prompt_hash": "fake"}


def gate_key(item_id: str, floor: bool = False) -> str:
    return f"{'floor' if floor else 'gate'}|local|{item_id}"


# --- collect -----------------------------------------------------------------------------------------


def collect(out: Path, gate: Any, *, limit: int | None = None, floor: int = FLOOR_ITEMS, rd: RunData | None = None) -> None:
    rd = rd or RunData(VC_RUN)
    log = CallLog(out / "calls.jsonl")
    ledger = Ledger(log)
    items = rd.items[:limit] if limit is not None else rd.items
    shown = 0
    for index, item in enumerate(items):
        state = gate_state(rd.texts(item), item["question"])
        row = run_call(log, ledger, gate_key(item["item_id"]), "gate", {"target": item["item_id"]},
                       lambda state=state: gate.read(state))
        if shown < 3 and row.get("status") == "ok":
            print(f"  raw reading — {item['family']} choice={row['choice']} shares={row['shares']} mass={row['mass']}")
            shown += 1
        if (index + 1) % 50 == 0:
            print(f"  {index + 1}/{len(items)}")
    for item in items[: min(floor, len(items))]:
        state = gate_state(rd.texts(item), item["question"])
        run_call(log, ledger, gate_key(item["item_id"], floor=True), "gate", {"target": item["item_id"]},
                 lambda state=state: gate.read(state))
    print(f"wrote {out / 'calls.jsonl'}")


# --- report ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    label: str | None
    gen_calls: int
    local_calls: int
    usd: float
    abstained: bool = False

    @property
    def wrong(self) -> bool:
        return self.label == "wrong"


def decline_label(family: str) -> str:
    """A shipped decline, labelled as the item design and the grader rubric fix it (§2)."""
    return "declined" if family == "ANS" else "correct"


Rule = Callable[[dict[str, Any]], bool]
RULES: dict[str, Rule] = {
    "argmax": lambda g: g.get("choice") == "sufficient",
    "p>=0.2": lambda g: (g.get("p") or 0.0) >= 0.2,
    "p>=0.8": lambda g: (g.get("p") or 0.0) >= 0.8,
}


def _d_row(rd: RunData, item: dict[str, Any]) -> Row:
    out, _ = outcomes(rd, item)["D_decl"]
    calls = out.calls
    gen = sum(1 for c in calls if ":" not in c)
    loc = len(calls) - gen
    if out.shipped is None:
        return Row(None, gen, loc, 0.0)
    if out.shipped in ("handoff", "decline"):
        return Row(decline_label(item["family"]), gen, loc, policy_cost(rd, item, out), abstained=True)
    return Row(rd.label(item["item_id"], out.shipped)[0], gen, loc, policy_cost(rd, item, out))


def item_rows(rd: RunData, item: dict[str, Any], gate: dict[str, Any] | None, rule: Rule) -> dict[str, Row]:
    iid, fam = item["item_id"], item["family"]
    l1 = rd.label(iid, "d1")[0]
    l2 = rd.label(iid, "d2")[0]
    c1 = rd.cost(f"draft|luna|{iid}|d1")
    c2 = rd.cost(f"draft|luna|{iid}|d2")
    decline = Row(decline_label(fam), 0, 0, 0.0, abstained=True)
    rows = {"A": Row(l1, 1, 0, c1), "A2": Row(l2, 1, 0, c2), "D": _d_row(rd, item), "ALL-DECLINE": decline}
    if gate is not None:
        passed = rule(gate)
        blocked = Row(decline_label(fam), 0, 1, 0.0, abstained=True)
        d = rows["D"]
        rows["C"] = Row(l1, 1, 1, c1) if passed else blocked
        rows["C2"] = Row(l2, 1, 1, c2) if passed else blocked
        rows["C+D"] = Row(d.label, d.gen_calls, d.local_calls + 1, d.usd, d.abstained) if passed else blocked
    return rows


def gate_rows(out: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
    log = CallLog(out / "calls.jsonl")
    gates: dict[str, dict[str, Any]] = {}
    floors: dict[str, dict[str, Any]] = {}
    halts = 0
    for key, row in log.rows.items():
        kind, _, iid = key.split("|", 2)
        if row.get("status") != "ok":
            halts += kind == "gate"
            continue
        (gates if kind == "gate" else floors)[iid] = row
    return gates, floors, halts


def _paired(x: Sequence[bool], y: Sequence[bool]) -> dict[str, Any]:
    """X against Y on wrong: b = Y-only wrong (X fixed it), c = X-only wrong (X broke it)."""
    b = sum(1 for p, q in zip(x, y, strict=True) if q and not p)
    c = sum(1 for p, q in zip(x, y, strict=True) if p and not q)
    diff, lo, hi = newcombe_paired(x, y)
    return {"wrong_x": sum(x), "wrong_y": sum(y), "b": b, "c": c, "p": mcnemar_exact(b, c), "diff": diff, "ci95": [lo, hi]}


def _arm_summary(rows: list[Row], items: list[dict[str, Any]]) -> dict[str, Any]:
    fams = [i["family"] for i in items]
    ans = [r for r, f in zip(rows, fams, strict=True) if f == "ANS"]
    answered_all = [
        r for r, f in zip(rows, fams, strict=True)
        if not r.abstained and ((f == "ANS" and r.label in (*ANSWERED, "wrong")) or (f != "ANS" and r.label in ("wrong", "incomplete")))
    ]
    return {
        "n": len(rows), "wrong": sum(r.wrong for r in rows),
        "wrong_by_family": {f: sum(r.wrong for r, g in zip(rows, fams, strict=True) if g == f) for f in sorted(set(fams))},
        "coverage_ans_answered": sum(1 for r in ans if r.label in ANSWERED), "n_ans": len(ans),
        "coverage_all": len(answered_all) / len(rows) if rows else None,
        "selective_accuracy": (sum(1 for r in answered_all if r.label in ANSWERED) / len(answered_all)) if answered_all else None,
        "gen_calls": sum(r.gen_calls for r in rows), "local_calls": sum(r.local_calls for r in rows),
        "usd": round(sum(r.usd for r in rows), 6), "abstained": sum(r.abstained for r in rows),
    }


def _boot_auroc(pos: list[float], neg: list[float]) -> list[float]:
    rng = random.Random(SEED)
    vals = []
    for _ in range(BOOT):
        vals.append(auroc([rng.choice(pos) for _ in pos], [rng.choice(neg) for _ in neg]))
    vals.sort()
    return [vals[int(0.025 * BOOT)], vals[int(0.975 * BOOT) - 1]]


def _random_gate(items: list[dict[str, Any]], rows: dict[str, list[Row]], k: int) -> dict[str, Any]:
    """R (§13): abstain on k items uniformly at random, 1,000 draws; wrong shipped and coverage."""
    rng = random.Random(SEED)
    n = len(items)
    wrongs, covs = [], []
    for _ in range(DRAWS):
        blocked = set(rng.sample(range(n), k))
        w = cov = 0
        for i, (item, a) in enumerate(zip(items, rows["A"], strict=True)):
            if i in blocked:
                continue
            w += a.wrong
            cov += item["family"] == "ANS" and a.label in ANSWERED
        wrongs.append(w)
        covs.append(cov)
    return {"k": k, "wrong_median": statistics.median(wrongs), "wrong_range": [min(wrongs), max(wrongs)],
            "coverage_median": statistics.median(covs), "coverage_range": [min(covs), max(covs)],
            "wrongs": wrongs, "coverages": covs}


def _verdict(x: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
    c1 = x["wrong"] <= d["wrong"]
    c2 = x["coverage_ans_answered"] >= d["coverage_ans_answered"]
    c3 = x["gen_calls"] < d["gen_calls"]
    return {"wrong_le_D": c1, "coverage_ge_D": c2, "fewer_paid_calls": c3, "wins": c1 and c2 and c3}


def build(out: Path, rd: RunData | None = None) -> dict[str, Any]:
    rd = rd or RunData(VC_RUN)
    # §6 control: the replay reproduces the published wrong counts on every item before any gate read.
    full = {iid: item_rows(rd, rd.by_id[iid], None, RULES["argmax"]) for iid in rd.by_id}
    control = {}
    for arm, (wrong, n) in PUBLISHED.items():
        key = "D" if arm == "D_decl" else arm
        done = [r[key] for r in full.values() if r[key].label is not None]
        control[arm] = {"wrong": sum(r.wrong for r in done), "n": len(done), "published": [wrong, n]}
        if (control[arm]["wrong"], control[arm]["n"]) != (wrong, n):
            raise SystemExit(f"HALT: the replay of {arm} reads {control[arm]} — not the published number")

    gates, floors, halts = gate_rows(out)
    attempted = len(gates) + halts
    report: dict[str, Any] = {"control_replay": control, "gate_reads": len(gates), "gate_halts": halts,
                              "halt_rate": halts / attempted if attempted else None}
    gated = [i for i in rd.items if i["item_id"] in gates]
    analysis, excluded = [], Counter()
    for item in gated:
        r = item_rows(rd, item, gates[item["item_id"]], RULES["argmax"])
        if r["A"].label is None or r["D"].label is None:
            excluded[item["family"]] += 1
            continue
        analysis.append(item)
    report["analysis_n"] = len(analysis)
    report["excluded_by_family"] = dict(excluded)
    report["raw_readings"] = [
        {"item": i["item_id"], "family": i["family"], "choice": gates[i["item_id"]]["choice"],
         "shares": gates[i["item_id"]]["shares"], "mass": gates[i["item_id"]]["mass"]} for i in gated[:3]
    ]
    if not analysis:
        return report

    per_rule: dict[str, Any] = {}
    for name, rule in RULES.items():
        table: dict[str, list[Row]] = {}
        for item in analysis:
            for arm, row in item_rows(rd, item, gates[item["item_id"]], rule).items():
                table.setdefault(arm, []).append(row)
        arms = {arm: _arm_summary(rows, analysis) for arm, rows in table.items()}
        g = [gates[i["item_id"]] for i in analysis]
        passed = [rule(x) for x in g]
        fams = [i["family"] for i in analysis]
        l1 = [a.label for a in table["A"]]
        dlab = table["D"]
        ans_correct = [f == "ANS" and lab in ANSWERED for f, lab in zip(fams, l1, strict=True)]
        false_abst = sum(1 for ok, p in zip(ans_correct, passed, strict=True) if ok and not p)
        harmless = sum(1 for f, lab, p in zip(fams, l1, passed, strict=True) if f != "ANS" and lab == "correct" and not p)
        prevented = sum(1 for lab, p in zip(l1, passed, strict=True) if lab == "wrong" and not p)
        d_answered_lost = sum(1 for f, dr, p in zip(fams, dlab, passed, strict=True) if f == "ANS" and dr.label in ANSWERED and not p)
        n_ok = sum(ans_correct)
        per_rule[name] = {
            "arms": arms,
            "false_abstention": false_abst, "false_abstention_of": n_ok,
            "false_abstention_rate": false_abst / n_ok if n_ok else None,
            "false_abstention_wilson": list(wilson(false_abst, n_ok)) if n_ok else None,
            "harmless_abstentions": harmless, "wrong_prevented": prevented, "d_answers_lost_by_gate": d_answered_lost,
            "pass_by_family": {f: f"{sum(p for p, g2 in zip(passed, fams, strict=True) if g2 == f)}/{fams.count(f)}" for f in sorted(set(fams))},
            "pass_by_lang": {lang: f"{sum(p for p, i in zip(passed, analysis, strict=True) if i['lang'] == lang)}/{sum(1 for i in analysis if i['lang'] == lang)}"
                             for lang in sorted({i["lang"] for i in analysis})},
            "paired": {
                "C_vs_D": _paired([r.wrong for r in table["C"]], [r.wrong for r in table["D"]]),
                "C_vs_A": _paired([r.wrong for r in table["C"]], [r.wrong for r in table["A"]]),
                "C+D_vs_D": _paired([r.wrong for r in table["C+D"]], [r.wrong for r in table["D"]]),
                "C2_vs_A2": _paired([r.wrong for r in table["C2"]], [r.wrong for r in table["A2"]]),
            },
            "verdict_C": _verdict(arms["C"], arms["D"]), "verdict_C+D": _verdict(arms["C+D"], arms["D"]),
        }
        if name == "argmax":
            rnd = _random_gate(analysis, table, arms["C"]["abstained"])
            cw, cc = arms["C"]["wrong"], arms["C"]["coverage_ans_answered"]
            per_rule[name]["random_gate"] = {
                k: v for k, v in rnd.items() if k not in ("wrongs", "coverages")
            } | {"share_draws_wrong_le_C": sum(w <= cw for w in rnd["wrongs"]) / DRAWS,
                 "share_draws_coverage_ge_C": sum(c >= cc for c in rnd["coverages"]) / DRAWS}
    report["rules"] = per_rule

    p = {i["item_id"]: float(gates[i["item_id"]].get("p") or 0.0) for i in analysis}
    pos = [p[i["item_id"]] for i in analysis if i["family"] == "ANS"]
    au: dict[str, Any] = {}
    for name, fams_neg in (("ANS_vs_NC", ("NCR", "NCP")), ("ANS_vs_NCR", ("NCR",)), ("ANS_vs_NCP", ("NCP",))):
        neg = [p[i["item_id"]] for i in analysis if i["family"] in fams_neg]
        if pos and neg:
            au[name] = {"auroc": auroc(pos, neg), "ci95": _boot_auroc(pos, neg) if len(pos) > 1 and len(neg) > 1 else None,
                        "n_pos": len(pos), "n_neg": len(neg)}
    report["auroc"] = au

    masses = [float(gates[i["item_id"]].get("mass") or 0.0) for i in analysis]
    report["mass"] = {"median": statistics.median(masses), "below_0.5": sum(m < 0.5 for m in masses)}
    flips = [iid for iid, f in floors.items() if iid in gates and f.get("choice") != gates[iid].get("choice")]
    dps = [abs(float(f.get("p") or 0) - float(gates[iid].get("p") or 0)) for iid, f in floors.items() if iid in gates]
    report["floor"] = {"n": len(dps), "flips": len(flips), "max_abs_dp": max(dps) if dps else None}
    sweep = []
    for t in [x / 20 for x in range(21)]:
        rows = [item_rows(rd, i, gates[i["item_id"]], lambda g, t=t: (g.get("p") or 0.0) >= t)["C"] for i in analysis]
        s = _arm_summary(rows, analysis)
        sweep.append({"t": t, "wrong": s["wrong"], "coverage_ans_answered": s["coverage_ans_answered"],
                      "abstained": s["abstained"], "gen_calls": s["gen_calls"],
                      "coverage_all": s["coverage_all"], "selective_accuracy": s["selective_accuracy"]})
    report["sweep_diagnostic"] = sweep
    report["mechanism_active"] = per_rule["argmax"]["arms"]["C"]["abstained"] > 0
    report["partial"] = len(gated) < len(rd.items)
    report["verdict_withheld"] = report["partial"] or bool(report["halt_rate"] and report["halt_rate"] > 0.05)
    return report


def fmt(rep: dict[str, Any]) -> str:
    lines = ["PARTIAL RUN (smoke) — the verdict lines below are wiring, not a result"] if rep.get("partial") else []
    lines += [f"control (replay vs published): {rep['control_replay']}",
             f"gate reads {rep['gate_reads']} · halts {rep['gate_halts']} · analysis n {rep.get('analysis_n')} · excluded {rep.get('excluded_by_family')}"]
    for r in rep.get("raw_readings", []):
        lines.append(f"  raw: {r}")
    if "rules" not in rep:
        return "\n".join(lines)
    for name, block in rep["rules"].items():
        lines.append(f"\n=== rule {name}{'  (PRIMARY)' if name == 'argmax' else '  (diagnostic)'} ===")
        lines.append(f"  {'arm':12s} {'n':>4s} {'wrong':>5s} {'ANS ans.':>9s} {'abst':>5s} {'gen':>5s} {'local':>5s} {'US$':>8s}  wrong by family")
        for arm, s in block["arms"].items():
            lines.append(f"  {arm:12s} {s['n']:4d} {s['wrong']:5d} {s['coverage_ans_answered']:4d}/{s['n_ans']:<4d} {s['abstained']:5d} "
                         f"{s['gen_calls']:5d} {s['local_calls']:5d} {s['usd']:8.4f}  {s['wrong_by_family']}")
        lines.append(f"  false abstention {block['false_abstention']}/{block['false_abstention_of']} (Wilson {block['false_abstention_wilson']}) · "
                     f"harmless {block['harmless_abstentions']} · wrong prevented {block['wrong_prevented']} · D answers lost {block['d_answers_lost_by_gate']}")
        lines.append(f"  pass by family {block['pass_by_family']} · by lang {block['pass_by_lang']}")
        for k, v in block["paired"].items():
            lines.append(f"  paired {k:9s} wrong {v['wrong_x']} vs {v['wrong_y']} · fixed {v['b']} broke {v['c']} · p {v['p']:.4f} · diff {v['diff']:+.4f} [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]")
        lines.append(f"  verdict C   {block['verdict_C']}")
        lines.append(f"  verdict C+D {block['verdict_C+D']}")
        if "random_gate" in block:
            lines.append(f"  random gate {block['random_gate']}")
    lines.append(f"\nAUROC {rep['auroc']}")
    lines.append(f"mass {rep['mass']} · floor {rep['floor']} · mechanism active {rep['mechanism_active']} · verdict withheld {rep['verdict_withheld']}")
    lines.append("sweep (diagnostic): " + " ".join(f"{s['t']:.2f}:{s['wrong']}w/{s['coverage_ans_answered']}a/{s['gen_calls']}g" for s in rep["sweep_diagnostic"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="gate only the first N items (smoke)")
    ap.add_argument("--floor", type=int, default=FLOOR_ITEMS)
    ap.add_argument("--fake", action="store_true", help="offline fake gate (tests)")
    ap.add_argument("--out", type=Path, default=HERE / "results" / "run")
    args = ap.parse_args(argv)
    if args.collect:
        collect(args.out, FakeGate() if args.fake else LocalGate(), limit=args.limit, floor=args.floor)
    if args.report or not args.collect:
        rep = build(args.out)
        text = fmt(rep)
        print(text)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(json.dumps(rep, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        (args.out / "report.txt").write_text(text + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
