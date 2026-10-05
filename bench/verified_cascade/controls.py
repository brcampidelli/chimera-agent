"""The two controls `PREREGISTRATION-controls.md` registers for the shipped default (arm D), and its
leave-one-category-out. US$ 0: a replay of the logged calls, no model is called.

    python bench/verified_cascade/controls.py [DIR]     # default DIR: results/run

R1 escalates as many random items as D escalated (the plan's control). R2 permutes D's whole action
vector — keep d1, escalate, divert — so it matches every share D has, and counts the answerable items
it leaves unanswered next to the wrong answers it ships. Writes ``DIR/controls.json``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.verified_cascade.common import RESULTS  # noqa: E402
from bench.verified_cascade.harness import THRESHOLD  # noqa: E402
from bench.verified_cascade.replay import RunData, outcomes, shipped_label  # noqa: E402
from bench.verified_cascade.stats import mcnemar_exact, newcombe_paired, percentile  # noqa: E402

DRAWS = 1000
SEED_R1 = 3033
SEED_R2 = 3034
KEEP, ESCALATE, DIVERT = "keep", "escalate", "divert"
NOT_ANSWERED = "not_answered"
"""An escalation that ends in a hand-off: nothing was shipped, and that is never wrong."""


@dataclass(frozen=True)
class Item:
    item_id: str
    family: str
    doc: str
    lang: str
    a_label: str
    """What A shipped: d1's label."""
    d_label: str
    """What D shipped: a draft's label, ``handoff`` or ``decline``."""
    action: str
    """D's first-rung action: keep, escalate or divert."""
    esc_label: str
    """What escalating this item ships: f1's label if the local read of f1 accepts, else a hand-off."""


def _escalation_label(rd: RunData, item_id: str) -> str | None:
    r2 = rd.reading("local", item_id, "f1") if rd.text(item_id, "f1") is not None else None
    if r2 is None or r2.choice is None:
        return None
    if not r2.accepts(THRESHOLD):
        return NOT_ANSWERED
    label, _ = rd.label(item_id, "f1")
    return label


def build_items(rd: RunData, *, fill: str | None = None) -> list[Item]:
    """The set P: A and D both labelled and the escalation outcome defined (preregistration §P).

    With ``fill``, an item whose escalation outcome is undefined is kept and that outcome is set to
    ``fill``. This is the post-hoc sensitivity check, not the registered set. P's cut is conditioned
    on D's own decision: on this run every item it drops is one D *kept*, so f1 was never needed
    there, and five of those eleven are D-wrong. Dropping them removes a quarter of D's errors while
    removing 2.8% of the items. Only a random draw that happens to escalate such an item needs the
    missing outcome, so bounding it (wrong / not answered / correct) gives the full-set reading.
    """
    out: list[Item] = []
    for item in rd.items:
        iid = item["item_id"]
        arms = outcomes(rd, item)
        a_label = shipped_label(rd, item, arms["A"][0])
        d_out = arms["D"][0]
        d_label = shipped_label(rd, item, d_out)
        esc = _escalation_label(rd, iid)
        if esc is None:
            esc = fill
        if a_label is None or d_label is None or esc is None:
            continue
        r1 = rd.reading("local", iid, "d1")
        assert r1 is not None  # D has a label, so its first read exists
        if r1.accepts(THRESHOLD):
            action = KEEP
        elif r1.choice == "declined":
            action = DIVERT
        else:
            action = ESCALATE
        out.append(Item(iid, item["family"], item["doc"], item["lang"], a_label, d_label, action, esc))
    return out


def shipped_under(item: Item, action: str) -> str:
    """The label an action ships on this item, under D's own rungs."""
    if action == KEEP:
        return item.a_label
    if action == ESCALATE:
        return item.esc_label
    return "handoff"


def answered(label: str) -> bool:
    return label not in ("handoff", "decline", NOT_ANSWERED)


def wrong_count(items: Sequence[Item], actions: Sequence[str]) -> int:
    return sum(shipped_under(i, a) == "wrong" for i, a in zip(items, actions, strict=True))


def ans_unanswered(items: Sequence[Item], actions: Sequence[str]) -> int:
    return sum(i.family == "ANS" and not answered(shipped_under(i, a)) for i, a in zip(items, actions, strict=True))


def _distribution(values: Sequence[int], observed: int) -> dict[str, Any]:
    p5 = percentile([float(v) for v in values], 0.05)
    return {
        "observed": observed, "p5": p5, "median": percentile([float(v) for v in values], 0.5),
        "min": min(values), "max": max(values),
        "share_at_or_below_observed": sum(v <= observed for v in values) / len(values),
        "below_p5": observed < p5,
    }


def control_r1(items: Sequence[Item], *, draws: int = DRAWS, seed: int = SEED_R1) -> dict[str, Any]:
    """Random escalation of as many items as D escalated; every other item ships d1."""
    k = sum(i.action == ESCALATE for i in items)
    rng = random.Random(seed)
    idx = list(range(len(items)))
    values: list[int] = []
    for _ in range(draws):
        chosen = set(rng.sample(idx, k))
        values.append(wrong_count(items, [ESCALATE if j in chosen else KEEP for j in idx]))
    d_wrong = sum(i.d_label == "wrong" for i in items)
    return {"k_escalated": k, "wrong": _distribution(values, d_wrong)}


def control_r2(items: Sequence[Item], *, draws: int = DRAWS, seed: int = SEED_R2) -> dict[str, Any]:
    """D's action vector permuted over the items: every share matched, the placement random."""
    rng = random.Random(seed)
    actions = [i.action for i in items]
    wrongs: list[int] = []
    unanswered: list[int] = []
    for _ in range(draws):
        perm = actions[:]
        rng.shuffle(perm)
        wrongs.append(wrong_count(items, perm))
        unanswered.append(ans_unanswered(items, perm))
    shares = {a: actions.count(a) for a in (KEEP, ESCALATE, DIVERT)}
    return {
        "shares": shares,
        "wrong": _distribution(wrongs, wrong_count(items, actions)),
        "ans_not_answered": _distribution(unanswered, ans_unanswered(items, actions)),
    }


def paired_d_minus_a(items: Sequence[Item]) -> dict[str, Any]:
    wd = [i.d_label == "wrong" for i in items]
    wa = [i.a_label == "wrong" for i in items]
    b = sum(1 for d, a in zip(wd, wa, strict=True) if a and not d)
    c = sum(1 for d, a in zip(wd, wa, strict=True) if d and not a)
    diff, lo, hi = newcombe_paired(wd, wa) if items else (0.0, -1.0, 1.0)
    return {"n": len(items), "wrong_d": sum(wd), "wrong_a": sum(wa), "fixed": b, "broken": c,
            "p": mcnemar_exact(b, c), "diff": diff, "newcombe": [lo, hi]}


def leave_one_out(items: Sequence[Item], field: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for g in sorted({getattr(i, field) for i in items}):
        rest = [i for i in items if getattr(i, field) != g]
        alone = [i for i in items if getattr(i, field) == g]
        held = paired_d_minus_a(rest)
        out[g] = {
            "without": held, "alone": paired_d_minus_a(alone),
            "sign_lost": held["diff"] >= 0, "carried_by_this": held["p"] >= 0.05,
        }
    return out


def replay_mismatches(items: Sequence[Item]) -> int:
    """Items where D's own actions, applied through ``shipped_under``, do not reproduce what the
    registered replay says D shipped. Must be 0, or the controls permute a different policy than D."""
    return sum(
        (shipped_under(i, i.action) == "wrong") != (i.d_label == "wrong")
        or answered(shipped_under(i, i.action)) != answered(i.d_label)
        for i in items
    )


FILLS = ("wrong", NOT_ANSWERED, "correct")
"""The bounds for an undefined escalation outcome: the worst, the registered hand-off, the best."""


def excluded_from_p(registered: Sequence[Item], full: Sequence[Item]) -> dict[str, Any]:
    """What P leaves out of the full labelled set, by D's action and D's label."""
    kept = {i.item_id for i in registered}
    gone = [i for i in full if i.item_id not in kept]
    return {
        "n": len(gone),
        "by_action": {a: sum(i.action == a for i in gone) for a in (KEEP, ESCALATE, DIVERT)},
        "d_wrong": sum(i.d_label == "wrong" for i in gone),
        "a_wrong": sum(i.a_label == "wrong" for i in gone),
        "items": sorted(i.item_id for i in gone),
    }


def sensitivity_full_set(rd: RunData, registered: Sequence[Item]) -> dict[str, Any]:
    """Post-hoc (study 30 review): the controls on every item where A and D are labelled, with the
    missing escalation outcomes filled at each bound. Reported next to P, never in place of it."""
    by_fill: dict[str, Any] = {}
    full: list[Item] = []
    for fill in FILLS:
        full = build_items(rd, fill=fill)
        bad = replay_mismatches(full)
        if bad:
            raise SystemExit(f"{bad} items (fill {fill}): D's action model does not reproduce the replay")
        by_fill[fill] = {"n": len(full), "all": paired_d_minus_a(full),
                         "R1": control_r1(full), "R2": control_r2(full)}
    return {"excluded_from_P": excluded_from_p(registered, full), "by_fill": by_fill}


def build(out: Path) -> dict[str, Any]:
    rd = RunData(out)
    items = build_items(rd)
    bad = replay_mismatches(items)
    if bad:
        raise SystemExit(f"{bad} items: D's action model does not reproduce the registered replay")
    return {
        "n_P": len(items), "all": paired_d_minus_a(items),
        "R1": control_r1(items), "R2": control_r2(items),
        "leave_one_out": {f: leave_one_out(items, f) for f in ("family", "doc", "lang")},
        "sensitivity_full_set": sensitivity_full_set(rd, items),
    }


def fmt(rep: dict[str, Any]) -> str:
    def dist(d: dict[str, Any]) -> str:
        return (f"observed {d['observed']}  draws p5 {d['p5']:.1f} median {d['median']:.1f} "
                f"[{d['min']}, {d['max']}]  share of draws <= observed {d['share_at_or_below_observed']:.3f}  "
                f"below p5: {d['below_p5']}")

    a = rep["all"]
    lines = [f"P: {rep['n_P']} items; D {a['wrong_d']} wrong, A {a['wrong_a']}; fixed {a['fixed']} broken {a['broken']}; "
             f"diff {a['diff']:+.4f} [{a['newcombe'][0]:+.3f},{a['newcombe'][1]:+.3f}] p {a['p']:.4f}",
             f"R1 (random escalation of k={rep['R1']['k_escalated']}): wrong {dist(rep['R1']['wrong'])}",
             f"R2 (D's shares {rep['R2']['shares']} permuted): wrong {dist(rep['R2']['wrong'])}",
             f"R2 ANS not answered: {dist(rep['R2']['ans_not_answered'])}"]
    sens = rep["sensitivity_full_set"]
    ex = sens["excluded_from_P"]
    lines.append(f"\nnot in P: {ex['n']} items, D's actions {ex['by_action']}, D wrong {ex['d_wrong']}, A wrong {ex['a_wrong']}")
    for fill, r in sens["by_fill"].items():
        al = r["all"]
        lines.append(f"full set, missing escalation outcome = {fill}: n {r['n']} D {al['wrong_d']} A {al['wrong_a']} "
                     f"diff {al['diff']:+.4f} p {al['p']:.4f}")
        lines.append(f"  R1 wrong: {dist(r['R1']['wrong'])}")
        lines.append(f"  R2 wrong: {dist(r['R2']['wrong'])}")
        lines.append(f"  R2 ANS not answered: {dist(r['R2']['ans_not_answered'])}")
    for field, groups in rep["leave_one_out"].items():
        lines.append(f"\nleave-one-{field}-out:")
        for g, r in groups.items():
            w, al = r["without"], r["alone"]
            lines.append(f"  without {g:<10} n {w['n']:>3} D {w['wrong_d']:>2} A {w['wrong_a']:>2} "
                         f"{w['fixed']}/{w['broken']} diff {w['diff']:+.4f} [{w['newcombe'][0]:+.3f},{w['newcombe'][1]:+.3f}] "
                         f"p {w['p']:.4f} | alone n {al['n']:>3} D {al['wrong_d']:>2} A {al['wrong_a']:>2} "
                         f"{al['fixed']}/{al['broken']} | sign lost {r['sign_lost']} carried by it {r['carried_by_this']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, nargs="?", default=RESULTS / "run")
    args = ap.parse_args(argv)
    rep = build(args.out)
    (args.out / "controls.json").write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(fmt(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
