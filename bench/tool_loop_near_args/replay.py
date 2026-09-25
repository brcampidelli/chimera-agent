"""Replay recorded traces through three loop detectors, per PREREGISTRATION.md. Nothing here spends.

Run in WSL:  python3 bench/tool_loop_near_args/replay.py [--json bench/tool_loop_near_args/results/summary.json]

The detectors are loaded from files, not imported from the installed package:
- legacy: the rule before #577, frozen in ``detectors/``;
- fixed: main after #577, frozen in ``detectors/``;
- new: this tree's ``chimera/core/tool_loop.py``.

Each trace row is one agent run. Its tool calls are fed in order, and a detector's answer is the step of
its first fire. Everything after an escalation's model switch is ignored, because the live detector was
reset there.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HOMES = Path(os.path.expanduser("~/hb-homes"))
HB = Path(os.path.expanduser("~/harness-bench"))
FIX_DAY = "2026-09-24"  # the day #577 was measured and merged; earlier traces ran the legacy rule
WRITES = {"edit_file", "write_file", "apply_patch", "multi_edit", "create_file"}
FAMILIES = ("brk-legacy-strong", "brk-legacy-weak", "brk-fixed-strong", "brk-fixed-weak",
            "m6-stop-strong", "m6-stop-weak", "m6-esc-strong", "m6-esc-weak", "m6f-strong", "m6f-weak")
ESCALATING = ("m6-esc-", "m6f-")


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up while the class is built
    spec.loader.exec_module(mod)
    return mod


DETECTORS = {
    "legacy": _load("legacy_tool_loop", HERE / "detectors" / "legacy_tool_loop.py"),
    "fixed": _load("fixed_tool_loop", HERE / "detectors" / "fixed_tool_loop.py"),
    "new": _load("new_tool_loop", ROOT / "chimera" / "core" / "tool_loop.py"),
}


def family_of(home: str) -> tuple[str, str, str]:
    """(family, task, hid) from a home directory name like ``043-db-migration-safety-m6f-weak-r4``."""
    for fam in FAMILIES:
        marker = f"-{fam}-r"
        if marker in home:
            task, rest = home.split(marker, 1)
            return fam, task, f"{fam}-r{rest}"
    return "other", home, ""


def parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {"_raw": str(raw)}  # clipped mid-JSON: the clipped text, whole, is the argument
    return value if isinstance(value, dict) else {"_raw": str(raw)}


def calls_of(row: dict[str, Any], escalating: bool) -> tuple[list[tuple[int, str, dict[str, Any], str, bool]], int | None]:
    """The row's calls as (step, name, args, observation, ok), cut at an escalation's model switch.

    Returns the calls and the step index of the switch (None when the model never changed)."""
    steps = row.get("steps") or []
    first_model = next((s.get("model") for s in steps if s.get("model")), None)
    calls, switch = [], None
    for i, st in enumerate(steps):
        if escalating and first_model and st.get("model") and st.get("model") != first_model:
            switch = i
            break
        for t in st.get("tools") or []:
            calls.append((i, str(t.get("name")), parse_args(t.get("arguments")), str(t.get("observation") or ""),
                          bool(t.get("ok", True))))
    return calls, switch


def first_fire(mod: ModuleType, calls: list[tuple[int, str, dict[str, Any], str, bool]]) -> int | None:
    det = mod.ToolLoopDetector()
    for k, (_, name, args, obs, ok) in enumerate(calls):
        if det.record(name, args, obs, ok=ok).tripped:
            return k
    return None


def recorded_trip(row: dict[str, Any], calls: list[Any], switch: int | None, escalating: bool) -> int | None:
    """The step the live run recorded as its trip, or None."""
    if escalating and switch is not None:
        return calls[-1][0] if calls else None
    if row.get("stopped_reason") == "tool_loop" and calls:
        return calls[-1][0]
    return None


def outcome_of(hid: str, task: str) -> float | None:
    hits = glob.glob(str(HB / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    if not hits:
        return None
    v = (json.loads(Path(hits[0]).read_text(encoding="utf-8")).get("oracle_result") or {}).get("outcome_score")
    return float(v) if isinstance(v, int | float) else None


def tail_kind(calls: list[Any], k: int) -> str:
    """What the four calls ending at k were: 'writes, distinct args', 'reads, same target', …"""
    tail = calls[max(0, k - 3): k + 1]
    names = {c[1] for c in tail}
    kind = "writes" if names <= WRITES else ("mixed" if names & WRITES else "reads")
    distinct = len({json.dumps(c[2], sort_keys=True, default=str) for c in tail}) > 1
    return f"{kind}, {'distinct' if distinct else 'identical'} args"


def main() -> None:  # noqa: C901 — one pass, one report
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    fidelity: dict[str, Counter[str]] = defaultdict(Counter)
    disagreements: list[str] = []
    stops: dict[str, Counter[str]] = defaultdict(Counter)
    new_only_legacy: list[str] = []
    write_tail_reproduced: list[str] = []
    cost_rows: list[dict[str, Any]] = []
    scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    skipped = Counter()

    for home in sorted(p for p in HOMES.iterdir() if p.is_dir()):
        trace = home / "traces.jsonl"
        if not trace.is_file():
            skipped["no trace"] += 1
            continue
        fam, task, hid = family_of(home.name)
        escalating = fam.startswith(ESCALATING)
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                skipped["bad line"] += 1
                continue
            ts = str(row.get("ts") or "")
            if fam.startswith("brk-fixed"):
                rule = "fixed"
            elif fam != "other" or ts[:10] < FIX_DAY:
                rule = "legacy"
            else:
                skipped["unknown rule (from the fix day on, outside the families)"] += 1
                continue
            group = fam if fam != "other" else "other (before the fix)"
            calls, switch = calls_of(row, escalating)
            rec = recorded_trip(row, calls, switch, escalating)
            fires = {name: first_fire(mod, calls) for name, mod in DETECTORS.items()}
            fire_step = {name: (calls[k][0] if k is not None else None) for name, k in fires.items()}

            # F: the rule the run ran must reproduce its recorded trip, at step granularity.
            agree = fire_step[rule] == rec
            fidelity[group]["agree" if agree else "disagree"] += 1
            if not agree:
                disagreements.append(f"{group:28s} {home.name}  recorded step={rec}  replayed {rule} step={fire_step[rule]}")

            if rule == "legacy" and fires["legacy"] is not None and agree:
                k = fires["legacy"]
                by_fixed = fire_step["fixed"] == fire_step["legacy"]
                by_new = fire_step["new"] == fire_step["legacy"]
                label = "fixed reproduces" if by_fixed else ("new only" if by_new else "neither")
                kind = tail_kind(calls, k)
                stops[group][f"{label} · {kind}"] += 1
                if by_new and not by_fixed:
                    tail = calls[max(0, k - 3): k + 1]
                    new_only_legacy.append(f"{group:28s} {home.name}  " + " | ".join(
                        f"{c[1]} {json.dumps(c[2], sort_keys=True)[:60]}" for c in tail))
                if by_new and not by_fixed and kind.startswith("writes") and "distinct" in kind:
                    write_tail_reproduced.append(home.name)

            if rule == "fixed":
                score = outcome_of(hid, task)
                if score is not None:
                    scores[(fam, task)].append(score)
                fn, ff = fire_step["new"], fire_step["fixed"]
                if fn is not None and (ff is None or fn < ff):
                    steps = row.get("steps") or []
                    after = steps[fn + 1:]
                    k = fires["new"]
                    cost_rows.append({
                        "family": fam, "task": task, "home": home.name, "fire_step": fn,
                        "steps_total": len(steps), "steps_after": len(after),
                        "prompt_tokens_after": sum(int(s.get("prompt_tokens") or 0) for s in after),
                        "score": score, "stopped_reason": row.get("stopped_reason"),
                        "tail": [f"{c[1]} {json.dumps(c[2], sort_keys=True)[:60]}"
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
    for d in disagreements[:60]:
        print("   ", d)

    print("\n== legacy stops, classified (which rule would also stop there)")
    for group in sorted(stops):
        for label, n in sorted(stops[group].items()):
            print(f"  {group:28s} {n:4d}  {label}")
    print(f"\n  legacy stops the new rule reproduces and fixed does not: {len(new_only_legacy)}")
    for s in new_only_legacy:
        print("   ", s[:260])
    print(f"  criterion 2 (write-tail, distinct args, reproduced by new): {len(write_tail_reproduced)} "
          f"-> {'PASS' if not write_tail_reproduced else 'FAIL'}")

    medians = {key: statistics.median(v) for key, v in scores.items() if v}
    strong = [r for r in cost_rows if r["family"] == "brk-fixed-strong"]
    weak = [r for r in cost_rows if r["family"] == "brk-fixed-weak"]
    costly = [r for r in weak if r["score"] is not None and r["score"] > medians.get((r["family"], r["task"]), 1.0)]
    print("\n== cost: new-only fires in runs that continued under the fixed rule")
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
            "fidelity": {g: dict(c) for g, c in fidelity.items()},
            "gate_F": ok_all,
            "stops": {g: dict(c) for g, c in stops.items()},
            "new_only_legacy": new_only_legacy,
            "criterion_2_write_tail_reproduced": write_tail_reproduced,
            "cost_rows": cost_rows,
            "cell_medians": {f"{k[0]}|{k[1]}": v for k, v in medians.items()},
            "skipped": dict(skipped),
        }, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
