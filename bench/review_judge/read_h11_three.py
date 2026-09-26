"""The registered read of H11 (`PREREGISTRATION-h11.md`): the three-state arm against arm A, out of sample.

    python bench/review_judge/read_h11_three.py RUN_DIR [--pilot-dir PILOT_DIR] > RUN_DIR/read.txt

Stdlib only, no model calls, deterministic (every bootstrap is seeded). Written and committed before
the main run; what it computes is what the pre-registration names, and nothing it prints is chosen
after seeing the numbers. It refuses to read anything before two guards pass:

1. **The statistic reproduces a published number.** Fed the August arms A and C out of sample, the
   paired bootstrap here must return exactly what `read_h11.py` published for C − A in precision,
   +4.5 pp [+2.0, +7.1]. That is also the positive control: an effect of that size, on these 814
   items, with this method, is one the instrument has already shown it can see.
2. **The rows are the registered items.** Unique, out of sample by the August rows' own keys, the
   replay arm on exactly the first 200 of the seeded order, every call on the pinned provider, and the
   prompt hashes the pre-registration froze.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
sys.path.insert(0, str(HERE))

SEED = 20260819
DRAWS = 10_000
Z95 = 1.959963984540054
STATES = ("confirmed", "plausible", "refuted")
SCORE = {"refuted": 0, "plausible": 1, "confirmed": 2}
#: The two operating points, declared in advance: which three-state verdicts are KEPT.
OPS = {"drop only refuted": {"confirmed", "plausible"}, "keep only confirmed": {"confirmed"}}
NI_BOUND = -0.02  # paired lower bound on recall of correct comments, against A
N_ITEMS = 814
REPLAY_N = 200
MIN_PAIRED = 700
PROVIDER = "novita"
A_PROMPT_SHA = "60a2749ed8c9"
T_PROMPT_SHA = "5548fe16010b"
#: Fresh arm A against the published out-of-sample figures (92.4% kept correct, 17.0% caught bad):
#: the control reproduces them if it lands inside these bands (PREREGISTRATION-h11.md, control).
CONTROL = {"keeps correct": (0.884, 0.964), "catches bad": (0.090, 0.250)}
#: PREREGISTRATION-h11.md, "Predictions" — inclusive bands.
PREDICTED = {
    "T confirmed share": (0.55, 0.80),
    "T plausible share": (0.10, 0.35),
    "T refuted share": (0.05, 0.15),
    "drop only refuted: Δ precision": (-0.010, 0.020),
    "drop only refuted: Δ recall of correct": (-0.030, 0.030),
    "keep only confirmed: Δ precision": (0.010, 0.060),
    "keep only confirmed: Δ recall of correct": (-0.350, -0.100),
    "Δ AUC (T ordinal − A binary)": (0.020, 0.080),
    "replay flip rate": (0.05, 0.12),
    "fresh A keeps correct": (0.88, 0.96),
    "fresh A catches bad": (0.10, 0.25),
}


def pct(x: float) -> str:
    return f"{x:.1%}"


def pp(x: float) -> str:
    return f"{x * 100:+.1f}"


def wilson(k: int, n: int) -> tuple[float, float]:
    """`chimera/eval/anytime.py`'s Wilson interval."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + Z95 * Z95 / n
    c = (p + Z95 * Z95 / (2 * n)) / d
    m = (Z95 / d) * math.sqrt(p * (1 - p) / n + Z95 * Z95 / (4 * n * n))
    return (max(0.0, c - m), min(1.0, c + m))


def mcnemar_ci(only_base: int, only_treat: int, n: int) -> tuple[float, float]:
    """`chimera/eval/paired.py`'s interval (Wilson on the discordant pairs), as `read_full.py`."""
    m = only_base + only_treat
    if n == 0:
        return (-1.0, 1.0)
    if m == 0:
        return (0.0, 0.0)
    lo, hi = wilson(only_treat, m)
    return ((m / n) * (2 * lo - 1), (m / n) * (2 * hi - 1))


Pair = tuple[int, bool, bool]  # (label, kept by the baseline, kept by the treatment)


def _prec(bad: list[Pair], good: list[Pair], i: int) -> float:
    kept_good = sum(p[i] for p in good)
    kept = kept_good + sum(p[i] for p in bad)
    return kept_good / kept if kept else float("nan")


def _deltas(bad: list[Pair], good: list[Pair]) -> tuple[float, float]:
    """(Δ precision of the kept set, Δ recall of correct comments), treatment − baseline."""
    d_rec = (sum(p[2] for p in good) - sum(p[1] for p in good)) / len(good)
    return _prec(bad, good, 2) - _prec(bad, good, 1), d_rec


def paired(pairs: list[Pair]) -> dict[str, tuple[float, float, float]]:
    """Paired bootstrap over items, resampled within label — `read_h11.py`'s `paired_precision`,
    draw for draw, with recall of correct comments read off the same draws."""
    bad = [p for p in pairs if p[0] == 0]
    good = [p for p in pairs if p[0] == 1]
    rng = random.Random(SEED)
    dp, dr = [], []
    for _ in range(DRAWS):
        x, y = _deltas(rng.choices(bad, k=len(bad)), rng.choices(good, k=len(good)))
        dp.append(x)
        dr.append(y)
    dp.sort()
    dr.sort()
    p0, r0 = _deltas(bad, good)
    lo, hi = int(0.025 * DRAWS), int(0.975 * DRAWS) - 1
    return {"prec": (p0, dp[lo], dp[hi]), "rec": (r0, dr[lo], dr[hi])}


def adopt(prec: tuple[float, float, float], rec: tuple[float, float, float], floor_abs: float) -> bool:
    """The adoption rule, PREREGISTRATION-h11.md. Impossible with null deltas (checked below)."""
    return prec[1] > 0 and prec[0] > floor_abs and rec[1] >= NI_BOUND


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def key(r: dict[str, Any]) -> tuple[Any, ...]:
    return (r["repo"], r["pr"], r["path"], r["from_line"], r["to_line"], r["note"], r["label"])


def a_kept(call: dict[str, Any]) -> bool | None:
    return {"approve": True, "reject": False}.get(call["verdict"])


def t_state(call: dict[str, Any]) -> str | None:
    return call["verdict"] if call["verdict"] in STATES else None


# --- guards --------------------------------------------------------------------------------------


def guard_statistic() -> bool:
    import read_h11

    a = [r for r in read_h11.load("cautious") if not r["in_pilot"]]
    c = [r for r in read_h11.load("split") if not r["in_pilot"]]
    ref = read_h11.paired_precision(a, c)
    pairs = [(x["label"], x["verdict"] == "approve", y["verdict"] == "approve") for x, y in zip(a, c, strict=True)
             if x["verdict"] in ("approve", "reject") and y["verdict"] in ("approve", "reject")]
    mine = paired(pairs)["prec"]
    shown = [round(v * 100, 1) for v in mine]
    same = mine == ref and shown == [4.5, 2.0, 7.1]
    print(f"  August C − A in precision, out of sample: {pp(mine[0])} [{pp(mine[1])}, {pp(mine[2])}] "
          f"· read_h11.py {pp(ref[0])} [{pp(ref[1])}, {pp(ref[2])}] · published +4.5 [+2.0, +7.1] "
          f"{'ok' if same else 'MISMATCH'}")
    null = adopt((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.0)
    print(f"  the adoption rule on null deltas fires: {null} {'ok' if not null else 'MISMATCH'}")
    return same and not null


def guard_rows(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> bool:
    ok = True
    ids = [r["id"] for r in rows]
    unique = len(set(ids)) == len(ids)
    august = [r for r in load(RESULTS / "cautious" / "details.jsonl") if not r["in_pilot"]]
    theirs = {key(r) for r in august}
    inside = all(key(r) in theirs for r in rows) and not any(r["in_pilot"] for r in rows)
    replay_ok = all((r["order"] < REPLAY_N) == r["replay"] == ("A2" in r["arms"]) for r in rows)
    served = Counter(c.get("provider", "").lower() for r in rows for c in r["arms"].values()
                     if c["verdict"] != "call_failed")
    pinned = set(served) <= {PROVIDER}
    shas = manifest.get("prompt_sha", {})
    sha_ok = shas.get("A") == A_PROMPT_SHA and shas.get("T") == T_PROMPT_SHA
    print(f"  rows {len(rows)} (registered {N_ITEMS}) · unique {unique} · all out of sample by the August "
          f"keys {inside} · replay arm on exactly order < {REPLAY_N} {replay_ok}")
    print(f"  served by {dict(served)} · pinned to {PROVIDER} only {pinned}")
    print(f"  prompt sha A {shas.get('A')} T {shas.get('T')} · frozen {A_PROMPT_SHA} / {T_PROMPT_SHA} {sha_ok}")
    ok &= unique and inside and replay_ok and pinned and sha_ok
    return ok


# --- the read ------------------------------------------------------------------------------------


def answered(rows: list[dict[str, Any]]) -> dict[str, Any]:
    print("\n== 2. what each arm answered (unparsed and call_failed apart)")
    out: dict[str, Any] = {}
    for arm in ("A", "T", "A2"):
        calls = [r["arms"][arm] for r in rows if arm in r["arms"]]
        verdicts = Counter(c["verdict"] for c in calls)
        truncated = sum(bool(c.get("truncated")) for c in calls)
        retried = sum(c.get("attempts", 1) > 1 for c in calls)
        print(f"  {arm:<3} n={len(calls):<4} {dict(sorted(verdicts.items()))} · truncated {truncated} "
              f"· retried {retried}")
        out[arm] = {"n": len(calls), "verdicts": dict(verdicts), "truncated": truncated,
                    "unparsed": verdicts.get("unparsed", 0), "call_failed": verdicts.get("call_failed", 0)}
    modes = Counter(r["arms"]["T"].get("parse", "-") for r in rows)
    print(f"  T parsed by: {dict(modes)}")
    print("  T state by label:")
    for label, name in ((1, "correct"), (0, "incorrect")):
        states = Counter(r["arms"]["T"]["verdict"] for r in rows if r["label"] == label)
        n = sum(states.values())
        cells = " · ".join(f"{s} {states.get(s, 0)} ({states.get(s, 0) / n:.1%})" for s in STATES)
        print(f"    {name:<9} n={n:<4} {cells}")
    graded = [t_state(r["arms"]["T"]) for r in rows if t_state(r["arms"]["T"])]
    share = {s: graded.count(s) / len(graded) for s in STATES} if graded else {}
    out["T_share"] = share
    return out


def control(rows: list[dict[str, Any]]) -> dict[str, Any]:
    print("\n== 3. control — does the fresh arm A reproduce the published one? (same items)")
    august = {key(r): r["verdict"] for r in load(RESULTS / "cautious" / "details.jsonl") if not r["in_pilot"]}
    both = [(r, august[key(r)]) for r in rows if a_kept(r["arms"]["A"]) is not None
            and august.get(key(r)) in ("approve", "reject")]
    out: dict[str, Any] = {}
    for name, verdict_of in (("fresh A", lambda r, v: r["arms"]["A"]["verdict"]), ("August A", lambda r, v: v)):
        good = [verdict_of(r, v) == "approve" for r, v in both if r["label"] == 1]
        bad = [verdict_of(r, v) == "reject" for r, v in both if r["label"] == 0]
        kept_good, kept_bad = sum(good), len(bad) - sum(bad)
        keep, catch = kept_good / len(good), sum(bad) / len(bad)
        prec = kept_good / (kept_good + kept_bad)
        klo, khi = wilson(kept_good, len(good))
        print(f"  {name:<9} keeps correct {pct(keep)} [{pct(klo)}, {pct(khi)}] · catches bad {pct(catch)} "
              f"({sum(bad)}/{len(bad)}) · precision {pct(prec)}")
        out[name] = {"keeps_correct": keep, "catches_bad": catch, "precision": prec}
    same = sum(r["arms"]["A"]["verdict"] == v for r, v in both)
    print(f"  same verdict on {same}/{len(both)} items ({same / len(both):.1%}) — different day, different route")
    inside = all(lo <= out["fresh A"][k.replace(" ", "_")] <= hi for k, (lo, hi) in CONTROL.items())
    print(f"  control bands {CONTROL}: {'REPRODUCES' if inside else 'DOES NOT REPRODUCE'} the published arm A")
    out["reproduces"] = inside
    return out


def floor(rows: list[dict[str, Any]]) -> dict[str, Any]:
    print(f"\n== 4. replay floor — arm A twice on the first {REPLAY_N} items, same session")
    pairs: list[Pair] = []
    for r in rows:
        if "A2" not in r["arms"]:
            continue
        a, a2 = a_kept(r["arms"]["A"]), a_kept(r["arms"]["A2"])
        if a is not None and a2 is not None:
            pairs.append((r["label"], a, a2))
    flips = sum(p[1] != p[2] for p in pairs)
    flips_good = sum(p[1] != p[2] for p in pairs if p[0] == 1)
    flips_bad = sum(p[1] != p[2] for p in pairs if p[0] == 0)
    n_good = sum(p[0] == 1 for p in pairs)
    got = paired(pairs)
    fires = adopt(got["prec"], got["rec"], 0.0)
    print(f"  paired {len(pairs)} · flips {flips} ({flips / len(pairs):.1%}): {flips_good}/{n_good} correct, "
          f"{flips_bad}/{len(pairs) - n_good} incorrect")
    print(f"  A2 − A: Δ precision {pp(got['prec'][0])} [{pp(got['prec'][1])}, {pp(got['prec'][2])}] · "
          f"Δ recall of correct {pp(got['rec'][0])} [{pp(got['rec'][1])}, {pp(got['rec'][2])}]")
    print(f"  the adoption rule applied to the replay fires: {fires} "
          f"({'UNINFORMATIVE — the rule passes a re-run' if fires else 'ok'})")
    return {"n": len(pairs), "flips": flips, "flip_rate": flips / len(pairs), "prec": got["prec"],
            "rec": got["rec"], "rule_fires": fires, "floor_abs_dprec": abs(got["prec"][0])}


def primary(rows: list[dict[str, Any]], floor_abs: float) -> dict[str, Any]:
    print("\n== 5. primary — each operating point against arm A, paired over the same items")
    print(f"  paired bootstrap within label, seed {SEED}, {DRAWS:,} draws, percentile 95% (as read_h11.py);")
    print("  Δ recall also by McNemar-Wilson (chimera/eval/paired.py, as read_full.py) as a cross-check")
    base = [(r["label"], a_kept(r["arms"]["A"]), t_state(r["arms"]["T"])) for r in rows]
    base = [b for b in base if b[1] is not None and b[2] is not None]
    n_good = sum(b[0] == 1 for b in base)
    a_good_kept = sum(b[1] for b in base if b[0] == 1)
    a_bad_kept = sum(b[1] for b in base if b[0] == 0)
    a_prec, a_rec = a_good_kept / (a_good_kept + a_bad_kept), a_good_kept / n_good
    plo, phi = wilson(a_good_kept, a_good_kept + a_bad_kept)
    rlo, rhi = wilson(a_good_kept, n_good)
    n_bad = len(base) - n_good
    print(f"  paired items {len(base)} ({n_good} correct, {n_bad} incorrect) · keep-all precision "
          f"{pct(n_good / len(base))}")
    print(f"  A{'':<22} precision {pct(a_prec)} [{pct(plo)}, {pct(phi)}] · recall of correct {pct(a_rec)} "
          f"[{pct(rlo)}, {pct(rhi)}] · catches bad {pct(1 - a_bad_kept / n_bad)}")
    out: dict[str, Any] = {"n_paired": len(base), "A": {"precision": a_prec, "recall_correct": a_rec}}
    for op, keep in OPS.items():
        pairs: list[Pair] = [(label, a, state in keep) for label, a, state in base]
        tg = sum(p[2] for p in pairs if p[0] == 1)
        tb = sum(p[2] for p in pairs if p[0] == 0)
        t_prec, t_rec = tg / (tg + tb) if tg + tb else float("nan"), tg / n_good
        plo, phi = wilson(tg, tg + tb)
        rlo, rhi = wilson(tg, n_good)
        got = paired(pairs)
        only_a = sum(p[1] and not p[2] for p in pairs if p[0] == 1)
        only_t = sum(p[2] and not p[1] for p in pairs if p[0] == 1)
        mlo, mhi = mcnemar_ci(only_a, only_t, n_good)
        j_t = (1 - tb / n_bad) - (1 - t_rec)
        j_a = (1 - a_bad_kept / n_bad) - (1 - a_rec)
        verdict = adopt(got["prec"], got["rec"], floor_abs)
        print(f"  T, {op:<20} precision {pct(t_prec)} [{pct(plo)}, {pct(phi)}] · recall of correct "
              f"{pct(t_rec)} [{pct(rlo)}, {pct(rhi)}] · catches bad {pct(1 - tb / n_bad)} · keeps "
              f"{(tg + tb) / len(pairs):.1%}")
        print(f"     Δ precision {pp(got['prec'][0])} pp [{pp(got['prec'][1])}, {pp(got['prec'][2])}] · "
              f"Δ recall of correct {pp(got['rec'][0])} pp [{pp(got['rec'][1])}, {pp(got['rec'][2])}] "
              f"(McNemar-Wilson [{pp(mlo)}, {pp(mhi)}], discordant: A only {only_a}, T only {only_t}) · "
              f"ΔJ {pp(j_t - j_a)}")
        print(f"     rule: precision lower bound > 0 {got['prec'][1] > 0} · gain > replay floor "
              f"{pp(floor_abs)} pp {got['prec'][0] > floor_abs} · recall lower bound ≥ {pp(NI_BOUND)} pp "
              f"{got['rec'][1] >= NI_BOUND} → {'ADOPT' if verdict else 'not adopted'} at this point"
              f"{' · precision FELL (upper bound < 0)' if got['prec'][2] < 0 else ''}")
        out[op] = {"precision": t_prec, "recall_correct": t_rec, "d_prec": got["prec"], "d_rec": got["rec"],
                   "d_rec_mcnemar": (mlo, mhi), "discordant_correct": (only_a, only_t),
                   "d_j": j_t - j_a, "adopt": verdict}
    return out


def auc(good: list[int], bad: list[int]) -> float:
    cg, cb = Counter(good), Counter(bad)
    wins = sum(cg[x] * cb[y] * (1.0 if x > y else 0.5 if x == y else 0.0) for x in cg for y in cb)
    return wins / (len(good) * len(bad))


def secondary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    print("\n== 6. secondary (registered, not decided on)")
    base = [(r["label"], int(bool(a_kept(r["arms"]["A"]))), SCORE[t_state(r["arms"]["T"])], r["arms"]["T"])
            for r in rows if a_kept(r["arms"]["A"]) is not None and t_state(r["arms"]["T"])]
    good = [(a, t) for label, a, t, _ in base if label == 1]
    bad = [(a, t) for label, a, t, _ in base if label == 0]

    def d_auc(g: list[tuple[int, int]], b: list[tuple[int, int]]) -> float:
        return auc([x[1] for x in g], [x[1] for x in b]) - auc([x[0] for x in g], [x[0] for x in b])

    rng = random.Random(SEED)
    draws = sorted(d_auc(rng.choices(good, k=len(good)), rng.choices(bad, k=len(bad))) for _ in range(DRAWS))
    a_auc = auc([x[0] for x in good], [x[0] for x in bad])
    t_auc = auc([x[1] for x in good], [x[1] for x in bad])
    lo, hi = draws[int(0.025 * DRAWS)], draws[int(0.975 * DRAWS) - 1]
    print(f"  S1 separation: AUC A (binary) {a_auc:.3f} · T (three ordered states) {t_auc:.3f} · "
          f"Δ {pp(t_auc - a_auc)} points [{pp(lo)}, {pp(hi)}]")

    found = Counter((c["verdict"], c.get("quote_in_diff")) for _, _, _, c in base)
    print("  S3 quote in the diff shown, by state (True / False / empty):")
    for s in STATES:
        print(f"    {s:<10} {found.get((s, True), 0):>4} / {found.get((s, False), 0):>4} / {found.get((s, None), 0):>4}")
    for q in (True, False, None):
        conf = [label for label, _, t, c in base if t == 2 and c.get("quote_in_diff") is q]
        if conf:
            print(f"    confirmed with quote_in_diff={q!s:<5}: precision {pct(sum(conf) / len(conf))} (n={len(conf)})")

    # S4: sensitivity — an unparsed answer treated as KEPT (fail-open) in both arms.
    print("  S4 unparsed counted as kept (fail-open), both arms:")
    for op, keep in OPS.items():
        pairs: list[Pair] = []
        for r in rows:
            a_v, t_v = r["arms"]["A"]["verdict"], r["arms"]["T"]["verdict"]
            if "call_failed" in (a_v, t_v):
                continue
            pairs.append((r["label"], a_v != "reject", t_v == "unparsed" or t_v in keep))
        got = paired(pairs)
        print(f"    {op:<20} n={len(pairs)} Δ precision {pp(got['prec'][0])} [{pp(got['prec'][1])}, "
              f"{pp(got['prec'][2])}] · Δ recall of correct {pp(got['rec'][0])} [{pp(got['rec'][1])}, "
              f"{pp(got['rec'][2])}]")
    return {"auc_A": a_auc, "auc_T": t_auc, "d_auc": (t_auc - a_auc, lo, hi)}


def cost(rows: list[dict[str, Any]], pilot_dir: Path | None) -> dict[str, Any]:
    print("\n== 8. cost (tokens × the provider's listed price)")
    out: dict[str, Any] = {}
    total = 0.0
    for arm in ("A", "T", "A2"):
        calls = [r["arms"][arm] for r in rows if arm in r["arms"]]
        spend = sum(c.get("usd", 0.0) for c in calls)
        prompt = sum(c.get("prompt", 0) for c in calls)
        completion = sum(c.get("completion", 0) for c in calls)
        secs = [c["seconds"] for c in calls if "seconds" in c]
        med = sorted(secs)[len(secs) // 2] if secs else 0
        print(f"  {arm:<3} {len(calls)} calls · prompt {prompt:,} · completion {completion:,} · US$ {spend:.3f} "
              f"· US$ {spend / max(len(calls), 1):.5f}/call · median {med:.0f}s/call")
        out[arm] = {"calls": len(calls), "usd": spend, "prompt": prompt, "completion": completion}
        total += spend
    pilot = 0.0
    if pilot_dir is not None and (pilot_dir / "rows.jsonl").exists():
        pilot = sum(c.get("usd", 0.0) for r in load(pilot_dir / "rows.jsonl") for c in r["arms"].values())
    print(f"  main US$ {total:.3f} + pilot US$ {pilot:.3f} = US$ {total + pilot:.3f} (cap US$ 9.00)")
    out["main_usd"], out["pilot_usd"] = total, pilot
    return out


def band(value: float, lo: float, hi: float) -> str:
    return "BELOW" if value < lo else "ABOVE" if value > hi else "inside"


def predictions(ans: dict[str, Any], ctrl: dict[str, Any], fl: dict[str, Any], prim: dict[str, Any],
                sec: dict[str, Any]) -> None:
    print("\n== 9. predictions (bands fixed before the run)")
    measured = {
        "T confirmed share": ans["T_share"].get("confirmed", 0.0),
        "T plausible share": ans["T_share"].get("plausible", 0.0),
        "T refuted share": ans["T_share"].get("refuted", 0.0),
        "drop only refuted: Δ precision": prim["drop only refuted"]["d_prec"][0],
        "drop only refuted: Δ recall of correct": prim["drop only refuted"]["d_rec"][0],
        "keep only confirmed: Δ precision": prim["keep only confirmed"]["d_prec"][0],
        "keep only confirmed: Δ recall of correct": prim["keep only confirmed"]["d_rec"][0],
        "Δ AUC (T ordinal − A binary)": sec["d_auc"][0],
        "replay flip rate": fl["flip_rate"],
        "fresh A keeps correct": ctrl["fresh A"]["keeps_correct"],
        "fresh A catches bad": ctrl["fresh A"]["catches_bad"],
    }
    for name, (lo, hi) in PREDICTED.items():
        v = measured[name]
        print(f"  {name:<42} predicted [{lo:+.3f}, {hi:+.3f}] measured {v:+.3f} {band(v, lo, hi)}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--pilot-dir", type=Path)
    args = parser.parse_args()
    rows = sorted(load(args.run_dir / "rows.jsonl"), key=lambda r: r["order"])
    manifest = json.loads((args.run_dir / "manifest.json").read_text(encoding="utf-8"))

    print("== 1. guards")
    if not guard_statistic():
        sys.exit("GUARD FAILED — the statistic does not reproduce the published number; nothing is read")
    if not guard_rows(rows, manifest):
        sys.exit("GUARD FAILED — the rows are not the registered items; nothing is read")
    print("  GUARDS PASSED")

    ans = answered(rows)
    ctrl = control(rows)
    fl = floor(rows)
    prim = primary(rows, fl["floor_abs_dprec"])
    sec = secondary(rows)

    print("\n== 7. uninformative conditions")
    reasons = []
    for arm in ("A", "T"):
        for what in ("unparsed", "call_failed"):
            share = ans[arm][what] / N_ITEMS
            print(f"  {arm} {what} {ans[arm][what]}/{N_ITEMS} ({share:.1%}) {'> 10% FIRES' if share > 0.10 else ''}")
            if share > 0.10:
                reasons.append(f"{arm} {what} > 10%")
    if prim["n_paired"] < MIN_PAIRED:
        reasons.append(f"paired items {prim['n_paired']} < {MIN_PAIRED}")
    top = max(ans["T_share"].values()) if ans["T_share"] else 1.0
    if top > 0.98:
        reasons.append("T answered one state to > 98% of items")
    if fl["rule_fires"]:
        reasons.append("the adoption rule passes the replay")
    if len(rows) < N_ITEMS:
        print(f"  INCOMPLETE: {len(rows)} of {N_ITEMS} items (stop rule); the read is over a seeded-random prefix")
    print(f"  paired items {prim['n_paired']} (≥ {MIN_PAIRED}) · T's largest state share {top:.1%} · "
          f"replay passes the rule {fl['rule_fires']}")
    print(f"  → {'UNINFORMATIVE: ' + '; '.join(reasons) if reasons else 'none fires'}")

    spend = cost(rows, args.pilot_dir)
    predictions(ans, ctrl, fl, prim, sec)

    adopted = [op for op in OPS if prim[op]["adopt"]]
    if reasons:
        decision = "UNINFORMATIVE — no decision (" + "; ".join(reasons) + ")"
    elif adopted:
        decision = "ADOPTED at: " + ", ".join(adopted)
    else:
        decision = "NOT ADOPTED at either operating point"
    print(f"\n== 10. decision under the registered rule: {decision}")

    summary = {"decision": decision, "rows": len(rows), "answered": ans, "control": ctrl, "floor": fl,
               "primary": prim, "secondary": sec, "cost": spend, "uninformative": reasons}
    (args.run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=list), encoding="utf-8")


if __name__ == "__main__":
    main()
