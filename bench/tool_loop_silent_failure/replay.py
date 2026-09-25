"""Replay recorded traces for the answered-failure rule, per PREREGISTRATION.md. Nothing here spends.

Run in WSL from the repo root:  python3 bench/tool_loop_silent_failure/replay.py [--json results/summary.json]

The trace helpers (family, args, calls, recorded trip, outcome, tail kind) are the registered ones of
``bench/tool_loop_near_args/replay.py``, imported rather than copied. Four detectors:
- legacy and fixed, frozen in ``bench/tool_loop_near_args/detectors`` (fidelity gate);
- near: main after #583, frozen in ``detectors/`` here (the baseline this change is measured against);
- new: this tree's ``chimera/core/tool_loop.py``.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "bench" / "tool_loop_near_args"))

import replay as base  # noqa: E402

DETECTORS = {
    "legacy": base.DETECTORS["legacy"],
    "fixed": base.DETECTORS["fixed"],
    "near": base._load("near_tool_loop", HERE / "detectors" / "near_tool_loop.py"),
    "new": base._load("silent_tool_loop", ROOT / "chimera" / "core" / "tool_loop.py"),
}


def main() -> None:  # noqa: C901 — one pass, one report
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    answered = DETECTORS["new"]._answered_failure
    fidelity: dict[str, Counter[str]] = defaultdict(Counter)
    disagreements: list[str] = []
    stops: dict[str, Counter[str]] = defaultdict(Counter)
    new_only_legacy: list[str] = []
    write_tail_reproduced: list[str] = []
    cost_rows: list[dict[str, Any]] = []
    scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    acted: Counter[str] = Counter()
    calls_seen = 0
    skipped: Counter[str] = Counter()

    for home in sorted(p for p in base.HOMES.iterdir() if p.is_dir()):
        trace = home / "traces.jsonl"
        if not trace.is_file():
            skipped["no trace"] += 1
            continue
        fam, task, hid = base.family_of(home.name)
        escalating = fam.startswith(base.ESCALATING)
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                skipped["bad line"] += 1
                continue
            ts = str(row.get("ts") or "")
            if fam.startswith("brk-fixed"):
                rule = "fixed"
            elif fam != "other" or ts[:10] < base.FIX_DAY:
                rule = "legacy"
            else:
                skipped["unknown rule (from the fix day on, outside the families)"] += 1
                continue
            group = fam if fam != "other" else "other (before the fix)"
            calls, switch = base.calls_of(row, escalating)
            for _, name, _, obs, ok in calls:
                calls_seen += 1
                if ok and answered(obs):
                    acted[name] += 1
            rec = base.recorded_trip(row, calls, switch, escalating)
            fires = {name: base.first_fire(mod, calls) for name, mod in DETECTORS.items()}
            step = {name: (calls[k][0] if k is not None else None) for name, k in fires.items()}

            agree = step[rule] == rec
            fidelity[group]["agree" if agree else "disagree"] += 1
            if not agree:
                disagreements.append(f"{group:28s} {home.name}  recorded step={rec}  replayed {rule} step={step[rule]}")

            if rule == "legacy" and fires["legacy"] is not None and agree:
                k = fires["legacy"]
                by_near = step["near"] == step["legacy"]
                by_new = step["new"] == step["legacy"]
                label = "near reproduces" if by_near else ("new only" if by_new else "neither")
                kind = base.tail_kind(calls, k)
                stops[group][f"{label} · {kind}"] += 1
                if by_new and not by_near:
                    tail = calls[max(0, k - 3): k + 1]
                    new_only_legacy.append(f"{group:28s} {home.name}  " + " | ".join(
                        f"{c[1]} {json.dumps(c[2], sort_keys=True)[:50]} -> {c[3][:40]!r}" for c in tail))
                    if kind.startswith("writes") and "distinct" in kind:
                        write_tail_reproduced.append(home.name)

            if rule == "fixed":
                score = base.outcome_of(hid, task)
                if score is not None:
                    scores[(fam, task)].append(score)
                fn, fnear = step["new"], step["near"]
                if fn is not None and (fnear is None or fn < fnear):
                    steps = row.get("steps") or []
                    after = steps[fn + 1:]
                    k = fires["new"]
                    cost_rows.append({
                        "family": fam, "task": task, "home": home.name, "fire_step": fn,
                        "steps_total": len(steps), "steps_after": len(after),
                        "prompt_tokens_after": sum(int(s.get("prompt_tokens") or 0) for s in after),
                        "score": score, "stopped_reason": row.get("stopped_reason"),
                        "tail": [f"{c[1]} {json.dumps(c[2], sort_keys=True)[:50]} -> {c[3][:40]!r}"
                                 for c in calls[max(0, k - 3): k + 1]],
                    })

    print("== F: fidelity (the rule each run ran, replayed, against its recorded trip)")
    ok_all = True
    for group in sorted(fidelity):
        c = fidelity[group]
        n = c["agree"] + c["disagree"]
        share = c["agree"] / n if n else 0.0
        ok_all &= share >= 0.95
        print(f"  {group:28s} {c['agree']:4d}/{n:<4d} = {share:.1%}")
    print(f"  gate F: {'PASS' if ok_all else 'FAIL'}   disagreements: {len(disagreements)}")
    for d in disagreements[:40]:
        print("   ", d)

    print(f"\n== how much the rule acts: {sum(acted.values())} of {calls_seen} calls the loop saw as a "
          f"success read as an answered failure")
    for name, n in acted.most_common():
        print(f"  {name:24s} {n}")

    print("\n== legacy stops, classified (near = main after #583; new = this rule)")
    for group in sorted(stops):
        for label, n in sorted(stops[group].items()):
            print(f"  {group:28s} {n:4d}  {label}")
    print(f"\n  legacy stops the new rule reproduces and near does not: {len(new_only_legacy)}")
    for s in new_only_legacy:
        print("   ", s[:300])
    print(f"  criterion 2 (write-tail, distinct args, reproduced by new only): {len(write_tail_reproduced)} "
          f"-> {'PASS' if not write_tail_reproduced else 'FAIL'}")

    medians = {key: statistics.median(v) for key, v in scores.items() if v}
    strong = [r for r in cost_rows if r["family"] == "brk-fixed-strong"]
    weak = [r for r in cost_rows if r["family"] == "brk-fixed-weak"]
    costly = [r for r in weak if r["score"] is not None and r["score"] > medians.get((r["family"], r["task"]), 1.0)]
    print("\n== cost: new-only fires (vs near) in runs that continued under the fixed rule")
    print(f"  criterion 3 (strong): {len(strong)} -> {'PASS' if not strong else 'FAIL'}")
    print(f"  criterion 4 (weak): {len(weak)} fires, {len(costly)} above their cell median -> "
          f"{'PASS' if not costly else 'FAIL'}")
    for r in cost_rows:
        med = medians.get((r["family"], r["task"]))
        print(f"   {r['family']:18s} {r['home']}  fire@step {r['fire_step']}/{r['steps_total']}  "
              f"after: {r['steps_after']} steps, {r['prompt_tokens_after']} prompt tok  "
              f"score {r['score']} (cell median {med})  ended {r['stopped_reason']}")
        for t in r["tail"]:
            print("      ", t)
    print("\n  skipped:", dict(skipped))

    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps({
            "fidelity": {g: dict(c) for g, c in fidelity.items()}, "gate_F": ok_all,
            "acted": dict(acted), "calls_seen": calls_seen,
            "stops": {g: dict(c) for g, c in stops.items()},
            "new_only_legacy": new_only_legacy,
            "criterion_2_write_tail_reproduced": write_tail_reproduced,
            "cost_rows": cost_rows,
            "cell_medians": {f"{k[0]}|{k[1]}": v for k, v in medians.items()},
            "skipped": dict(skipped),
        }, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
