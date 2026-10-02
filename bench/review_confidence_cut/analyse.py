"""Offline re-analysis: a cut on the finder's confidence, read from reviews already stored.

No model is called. The two inputs are the run files of two earlier benches, committed with them:

- selection set: ``bench/review_reviewer/results/run.json``, arms D, L and Q, both replicas;
- confirmation set: ``bench/review_seeded/results/run.json``, cells D1, D2 and G.

What a cut does to one stored review: a finding is shown when the verifier did not drop it **and**
its confidence is at least the cut. A finding with no confidence is never cut (an unknown is not a
low). The verifier judged each finding on its own, one per call, so applying the cut before or
after it shows the same set; the product applies it before, which also saves the verifier's calls.

    python bench/review_confidence_cut/analyse.py --check   # positive control only, no cut read
    python bench/review_confidence_cut/analyse.py           # the registered analysis

`PREREGISTRATION.md` holds the grid, the rule and the reasons.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
SELECTION = BENCH / "review_reviewer" / "results" / "run.json"
CONFIRMATION = BENCH / "review_seeded" / "results" / "run.json"

#: The registered grid. ``None`` is today's behaviour, no cut.
GRID: tuple[float, ...] = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9)

# The registered rule (PREREGISTRATION.md, "The frozen rule").
SEEDS_KEPT_POOLED = 0.95
SEEDS_KEPT_PER_ARM = 0.90
CLEAN_REMOVED_POOLED = 0.50
CONFIRM_SEEDS_KEPT = 0.90
CONFIRM_CLEAN_REMOVED = 0.30

#: Positive control: with no cut, the figures each source bench published.
#: review_reviewer RESULTS.md: shown seeds and clean findings shown (both replicas, 10 diffs each).
PUBLISHED_SELECTION = {"D": (34, 40, 19), "L": (39, 40, 17), "Q": (27, 38, None)}
#: review_seeded RESULTS.md: end-to-end recall, and clean findings after the verifier.
PUBLISHED_CONFIRMATION = {"D1": (17, 20, 8), "D2": (17, 19, 12), "G": (11, 11, 8)}

#: A clean diff that was not clean (review_seeded RESULTS.md, "What the hand read found"): the
#: allowlist finding there is true, so cutting it is a loss the precision proxy books as a gain.
KNOWN_TRUE_CLEAN = ("924aaa76", "chimera/governance/allowlist.py")


def shown(v: dict[str, Any], cut: float | None) -> bool:
    """Shown by the product: not dropped by the verifier, and not under the cut."""
    if v["verdict"] == "dropped":
        return False
    conf = v.get("confidence")
    return cut is None or conf is None or conf >= cut


# --- the two sets, as (cell, row, turn) for every completed review -----------------------------


def _selection(data: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    out = []
    for row in data["rows"]:
        for arm in ("D", "L", "Q"):
            for turn in row["runs"][arm][:2]:
                # review_reviewer's `_done`: it ran, and its status is not incomplete.
                if turn["status"] not in ("budget", "stopped", "error", "incomplete"):
                    out.append((arm, row, turn))
    return out


def _confirmation(data: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    out = []
    for row in data["rows"]:
        for cell, (arm, rep) in {"D1": ("D", 0), "D2": ("D", 1), "G": ("G", 0)}.items():
            runs = row["runs"][arm]
            # review_seeded's `_cell`: the turn ran and did not halt.
            if len(runs) > rep and runs[rep]["status"] not in ("error", "incomplete", "budget"):
                out.append((cell, row, runs[rep]))
    return out


def tally(
    turns: list[tuple[str, dict[str, Any], dict[str, Any]]], cut: float | None
) -> dict[str, dict[str, int]]:
    """Per cell: seeded reviews, seeds shown, clean diffs, clean findings shown, verifier calls."""
    out: dict[str, dict[str, int]] = {}
    for cell, row, turn in turns:
        c = out.setdefault(cell, {"seeded": 0, "seeds": 0, "clean": 0, "clean_shown": 0,
                                  "checks": 0, "other": 0})
        if row["seed"]:
            c["seeded"] += 1
            c["seeds"] += any(shown(v, cut) for v in turn["verified"])
            # Anchored findings that miss the seed: unverified, truth unknown; reported only.
            hit_keys = {(h["file"], h["line"], h["title"]) for h in turn["hits"]}
            c["other"] += sum(1 for f in turn["findings"] if f["anchor"] == "kept"
                              and (f["file"], f["line"], f["title"]) not in hit_keys
                              and (cut is None or f.get("confidence") is None
                                   or f["confidence"] >= cut))
        else:
            c["clean"] += 1
            c["clean_shown"] += sum(1 for v in turn["verified"] if shown(v, cut))
        # Verifier calls the product would make: findings that reach it past the cut.
        c["checks"] += sum(1 for v in turn["verified"]
                           if cut is None or v.get("confidence") is None
                           or v["confidence"] >= cut)
    return out


def _pool(t: dict[str, dict[str, int]], key: str) -> int:
    return sum(c[key] for c in t.values())


def _ratio(after: int, before: int) -> float:
    return after / before if before else 1.0


# --- the positive control ----------------------------------------------------------------------


def control(sel: dict[str, Any], conf: dict[str, Any]) -> bool:
    ok = True
    base = tally(_selection(sel), None)
    for arm, (seeds, seeded, clean) in PUBLISHED_SELECTION.items():
        got = base[arm]
        match = got["seeds"] == seeds and got["seeded"] == seeded and (
            clean is None or got["clean_shown"] == clean)
        ok &= match
        print(f"  selection {arm}: seeds shown {got['seeds']}/{got['seeded']} (published "
              f"{seeds}/{seeded}); clean shown {got['clean_shown']} over {got['clean']} diffs "
              f"(published {clean if clean is not None else 'per-replica 13.0 per 10'}) "
              f"{'ok' if match else 'MISMATCH'}")
    base = tally(_confirmation(conf), None)
    for cell, (seeds, seeded, clean) in PUBLISHED_CONFIRMATION.items():
        got = base[cell]
        match = (got["seeds"], got["seeded"], got["clean_shown"]) == (seeds, seeded, clean)
        ok &= match
        print(f"  confirmation {cell}: seeds shown {got['seeds']}/{got['seeded']} (published "
              f"{seeds}/{seeded}); clean shown {got['clean_shown']} (published {clean}) "
              f"{'ok' if match else 'MISMATCH'}")
    return ok


# --- the registered analysis -------------------------------------------------------------------


def _line(name: str, base: dict[str, dict[str, int]], t: dict[str, dict[str, int]]) -> str:
    cells = []
    for cell in base:
        b, a = base[cell], t[cell]
        cells.append(f"{cell} {a['seeds']}/{b['seeds']} · {a['clean_shown']}/{b['clean_shown']}")
    s0, s1 = _pool(base, "seeds"), _pool(t, "seeds")
    c0, c1 = _pool(base, "clean_shown"), _pool(t, "clean_shown")
    k0, k1 = _pool(base, "checks"), _pool(t, "checks")
    return (f"  {name:>5}  seeds kept {s1}/{s0} = {_ratio(s1, s0):.1%}  clean removed "
            f"{c0 - c1}/{c0} = {1 - _ratio(c1, c0):.1%}  checks {k1}/{k0}  | " + "  ".join(cells))


def passes_selection(base: dict[str, dict[str, int]], t: dict[str, dict[str, int]]) -> bool:
    pooled = _ratio(_pool(t, "seeds"), _pool(base, "seeds"))
    per_arm = all(_ratio(t[a]["seeds"], base[a]["seeds"]) >= SEEDS_KEPT_PER_ARM for a in base)
    removed = 1 - _ratio(_pool(t, "clean_shown"), _pool(base, "clean_shown"))
    return pooled >= SEEDS_KEPT_POOLED and per_arm and removed >= CLEAN_REMOVED_POOLED


def passes_seeds_only(base: dict[str, dict[str, int]], t: dict[str, dict[str, int]]) -> bool:
    pooled = _ratio(_pool(t, "seeds"), _pool(base, "seeds"))
    per_arm = all(_ratio(t[a]["seeds"], base[a]["seeds"]) >= SEEDS_KEPT_PER_ARM for a in base)
    return pooled >= SEEDS_KEPT_POOLED and per_arm


def _removed(base: dict[str, dict[str, int]], t: dict[str, dict[str, int]]) -> float:
    return 1 - _ratio(_pool(t, "clean_shown"), _pool(base, "clean_shown"))


def analyse(sel: dict[str, Any], conf: dict[str, Any]) -> None:
    s_turns, c_turns = _selection(sel), _confirmation(conf)
    s_base, c_base = tally(s_turns, None), tally(c_turns, None)

    print("\nSELECTION (review_reviewer D, L, Q; both replicas). Per cell: seeds shown after/"
          "before · clean findings shown after/before")
    s_tallies = {cut: tally(s_turns, cut) for cut in GRID}
    for cut, t in s_tallies.items():
        mark = "passes" if passes_selection(s_base, t) else ""
        print(_line(f"{cut}", s_base, t) + (f"   <- {mark}" if mark else ""))

    passing = [c for c in GRID if passes_selection(s_base, s_tallies[c])]
    chosen = max(passing, key=lambda c: (_removed(s_base, s_tallies[c]), -c)) if passing else None
    print(f"\n  cuts passing the selection rule: {passing or 'none'}; chosen: {chosen}")

    print("\nCONFIRMATION (review_seeded D1, D2, G)")
    for cut in GRID:
        print(_line(f"{cut}", c_base, tally(c_turns, cut)))

    confirmed = False
    if chosen is not None:
        t = tally(c_turns, chosen)
        kept = _ratio(_pool(t, "seeds"), _pool(c_base, "seeds"))
        removed = _removed(c_base, t)
        confirmed = kept >= CONFIRM_SEEDS_KEPT and removed >= CONFIRM_CLEAN_REMOVED
        print(f"\n  at the chosen cut {chosen}: seeds kept {kept:.1%} (bar "
              f"{CONFIRM_SEEDS_KEPT:.0%}), clean removed {removed:.1%} (bar "
              f"{CONFIRM_CLEAN_REMOVED:.0%}) -> {'CONFIRMED' if confirmed else 'NOT confirmed'}")
        medium = chosen
    else:
        seeds_ok = [c for c in GRID if passes_seeds_only(s_base, s_tallies[c])]
        medium = (max(seeds_ok, key=lambda c: (_removed(s_base, s_tallies[c]), -c))
                  if seeds_ok else None)

    print("\nDECISION (by the frozen rule)")
    print(f"  medium's cut: {medium if medium is not None else 'none: medium is not offered'}")
    print(f"  default effort: {'medium' if chosen is not None and confirmed else 'high'}")

    _reported(s_turns, c_turns, chosen if chosen is not None else 0.7)


def _reported(s_turns: list[Any], c_turns: list[Any], cut: float) -> None:
    print(f"\nREPORTED, NOT DECIDED ON (at cut {cut})")
    for name, turns in (("selection", s_turns), ("confirmation", c_turns)):
        hits = [v.get("confidence") for _, r, t in turns if r["seed"] for v in t["verified"]
                if v["verdict"] != "dropped"]
        clean = [v.get("confidence") for _, r, t in turns if not r["seed"] for v in t["verified"]
                 if v["verdict"] != "dropped"]
        for label, xs in (("hit findings shown", hits), ("clean findings shown", clean)):
            known = sorted(x for x in xs if x is not None)
            hist: dict[float, int] = {}
            for x in known:
                hist[x] = hist.get(x, 0) + 1
            print(f"  {name}, {label}: n {len(xs)}, no confidence {len(xs) - len(known)}; "
                  + ", ".join(f"{k:g}:{n}" for k, n in sorted(hist.items())))
        base, t = tally(turns, None), tally(turns, cut)
        print(f"  {name}: other anchored findings on seeded diffs (truth unknown) "
              f"{_pool(base, 'other')} -> {_pool(t, 'other')}")
    commit, path = KNOWN_TRUE_CLEAN
    for name, turns in (("selection", s_turns), ("confirmation", c_turns)):
        for cell, row, turn in turns:
            if row["commit"] != commit:
                continue
            for v in turn["verified"]:
                if v["file"] == path and "escape" in (v["title"] + v["evidence"]).lower():
                    print(f"  known-true clean finding, {name} {cell}: conf {v.get('confidence')}"
                          f", verifier {v['verdict']}, shown at the cut: {shown(v, cut)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="Positive control only.")
    args = ap.parse_args()
    sel = json.loads(SELECTION.read_text(encoding="utf-8"))
    conf = json.loads(CONFIRMATION.read_text(encoding="utf-8"))
    print("POSITIVE CONTROL (no cut; must reproduce the published figures)")
    ok = control(sel, conf)
    print(f"  -> {'reproduced' if ok else 'NOT reproduced: no decision is read'}")
    if args.check or not ok:
        return
    analyse(sel, conf)


if __name__ == "__main__":
    main()
