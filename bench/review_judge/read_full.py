"""The pre-registered read of the full-slice run: arms A and C, all / in-sample / out-of-sample.

    python bench/review_judge/read_full.py > bench/review_judge/results/full_read.txt

`PREREGISTRATION-full.md` fixed, before either arm ran, that every result is reported three times —
over every graded row, over the 105 items arm C's rubric was written against, and over the rest —
and that the out-of-sample figure is the headline. Both arms finished; their all-rows numbers went
into commit messages, and the in/out split was never computed. This file is that registered read.

Stdlib only, no model calls, deterministic (the one bootstrap is seeded). It reads:

    results/cautious/{details.jsonl,summary.json}          arm A over the full slice
    results/split/{details.jsonl,summary.json}             arm C over the full slice
    results/{neutral,nodefect,preexisting}/details.jsonl   the pilot's 105, to check the mark
    git: the pilot's own arm A and arm C details           overwritten in place by the full run

and it refuses to go on unless it first reproduces both summary.json files from details.jsonl.
"""

from __future__ import annotations

import json
import math
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ARMS = {"A": "cautious", "C": "split"}
PILOT_ARMS = ("neutral", "nodefect", "preexisting")

#: The pilot's arm A and arm C details, as committed before the full run overwrote the same paths.
PILOT_HISTORY = {"A": ("3ba341bf", "results/cautious/details.jsonl"),
                 "C": ("029e89f6", "results/split/details.jsonl")}
#: What RESULTS.md published for the pilot — the "in sample (known)" column of the prediction table.
PILOT_PUBLISHED = {"A": {"caught": 8, "bad": 53, "wrongly": 0, "good": 52},
                   "C": {"caught": 32, "bad": 53, "wrongly": 20, "good": 52}}

SEED = 20260819  # the bench's own seed, fixed in PREREGISTRATION.md
DRAWS = 10_000
Z95 = 1.959963984540054  # chimera/eval/anytime.py, so the intervals are the ones the runner printed

PLANNED = {"all": 1017, "in-sample": 105, "out-of-sample": 912}
MIN_SURVIVING = 700
CEILING = 0.20
#: PREREGISTRATION-full.md, "Predictions, before the run" — out-of-sample bands, inclusive.
PREDICTED = {
    "A recall": (0.12, 0.20),
    "C recall": (0.45, 0.60),
    "C false rejection": (0.32, 0.45),
    "C - A in J": (0.02, 0.07),
}

Key = tuple[str, int, str, int, int, str, int]


def key(row: dict[str, Any]) -> Key:
    """`row_id` is 0 on every row (the dataset has no `__index__`), so an item is its content."""
    return (row["repo"], row["pr"], row["path"], row["from_line"], row["to_line"], row["note"],
            row["label"])


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def wilson(k: int, n: int) -> tuple[float, float]:
    """The Wilson score interval exactly as `chimera/eval/anytime.py` computes it."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + Z95 * Z95 / n
    center = (p + Z95 * Z95 / (2 * n)) / denom
    margin = (Z95 / denom) * math.sqrt(p * (1 - p) / n + Z95 * Z95 / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def paired_ci(only_base: int, only_treat: int, n: int) -> tuple[float, float]:
    """`chimera/eval/paired.py`'s McNemar interval: Wilson on the discordant pairs, mapped to Δ.

    The method `PREREGISTRATION-arms.md` and `-rubric.md` fixed for every paired rate here.
    """
    m = only_base + only_treat
    if n == 0:
        return (-1.0, 1.0)
    if m == 0:
        return (0.0, 0.0)
    lo, hi = wilson(only_treat, m)
    return ((m / n) * (2 * lo - 1), (m / n) * (2 * hi - 1))


def rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The runner's own arithmetic (`report_results`), on any subset of graded rows."""
    graded = [r for r in rows if r["verdict"] in ("approve", "reject")]
    bad = [r for r in graded if r["label"] == 0]
    good = [r for r in graded if r["label"] == 1]
    caught = sum(r["verdict"] == "reject" for r in bad)
    wrongly = sum(r["verdict"] == "reject" for r in good)
    recall = caught / len(bad) if bad else 0.0
    false_rej = wrongly / len(good) if good else 0.0
    return {
        "n": len(rows), "n_graded": len(graded), "bad": len(bad), "caught": caught,
        "good": len(good), "wrongly": wrongly, "recall": recall, "false_rej": false_rej,
        "recall_ci": wilson(caught, len(bad)), "false_rej_ci": wilson(wrongly, len(good)),
        "reject_rate": sum(r["verdict"] == "reject" for r in graded) / len(graded) if graded else 0.0,
        "unparsed": sum(r["verdict"] == "unparsed" for r in rows),
        "call_failed": sum(r["verdict"] == "call_failed" for r in rows),
        "j": recall - false_rej,
    }


def guard(label: str, rows: list[dict[str, Any]], summary: dict[str, Any]) -> bool:
    """Reproduce summary.json from details.jsonl, field by field, before reading anything else."""
    got = rates(rows)
    mine = {
        "n_graded": got["n_graded"], "n_unparsed": got["unparsed"],
        "n_call_failed": got["call_failed"], "bad_comments": got["bad"], "caught": got["caught"],
        "rejection_recall": round(got["recall"], 3),
        "rejection_recall_ci": [round(x, 3) for x in got["recall_ci"]],
        "good_comments": got["good"], "wrongly_rejected": got["wrongly"],
        "false_rejection_rate": round(got["false_rej"], 3),
        "false_rejection_ci": [round(x, 3) for x in got["false_rej_ci"]],
        "reject_rate_overall": round(got["reject_rate"], 3),
        "approve_everything_accuracy": round(got["good"] / got["n_graded"], 3),
    }
    ok = True
    for field, value in mine.items():
        same = summary.get(field) == value
        ok &= same
        print(f"  {label}  {field:<28} details {value!s:<16} summary {summary.get(field)!s:<16} "
              f"{'ok' if same else 'MISMATCH'}")
    return ok


def check_mark(a_rows: list[dict[str, Any]], c_rows: list[dict[str, Any]]) -> bool:
    """The in-sample mark written by the runner must be exactly the pilot's graded items."""
    aligned = [key(a) for a in a_rows] == [key(c) for c in c_rows]
    same_mark = [a["in_pilot"] for a in a_rows] == [c["in_pilot"] for c in c_rows]
    unique = len({key(a) for a in a_rows}) == len(a_rows)
    marked = {key(a) for a in a_rows if a["in_pilot"]}
    print(f"  row_id distinct values in arm A: {len({a['row_id'] for a in a_rows})} "
          f"(every row is {a_rows[0]['row_id']}; items are keyed by content instead)")
    print(f"  A and C: same items in the same order {aligned} · same in_pilot mark {same_mark} "
          f"· every key unique {unique}")
    print(f"  rows marked in_pilot: {len(marked)}")
    ok = aligned and same_mark and unique
    for arm in PILOT_ARMS:
        pilot = {key(r) for r in load(RESULTS / arm / "details.jsonl")}
        print(f"  == the {len(pilot)} items the pilot graded in results/{arm}: {pilot == marked}")
        ok &= pilot == marked
    return ok


def pilot_from_git(arm: str) -> list[dict[str, Any]] | None:
    sha, rel = PILOT_HISTORY[arm]
    done = subprocess.run(["git", "show", f"{sha}:./{rel}"], cwd=HERE, capture_output=True,
                          text=True, encoding="utf-8")
    if done.returncode != 0:
        return None
    return [json.loads(x) for x in done.stdout.splitlines() if x.strip()]


def split(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {"all": rows, "in-sample": [r for r in rows if r["in_pilot"]],
            "out-of-sample": [r for r in rows if not r["in_pilot"]]}


def pct(x: float) -> str:
    return f"{x:.1%}"


def pp(x: float) -> str:
    return f"{x * 100:+.1f}"


def paired_deltas(a_rows: list[dict[str, Any]], c_rows: list[dict[str, Any]]
                  ) -> tuple[list[int], list[int]]:
    """Per item, C's rejection minus A's: over the bad comments (→ ΔTPR) and the good (→ ΔFPR)."""
    bad, good = [], []
    for a, c in zip(a_rows, c_rows, strict=True):
        d = (c["verdict"] == "reject") - (a["verdict"] == "reject")
        (bad if a["label"] == 0 else good).append(d)
    return bad, good


def bootstrap_dj(bad: list[int], good: list[int], rng: random.Random) -> list[float]:
    """Paired bootstrap of ΔJ over items, resampled within label so TPR and FPR keep their n."""
    nb, ng = len(bad), len(good)
    return [sum(rng.choices(bad, k=nb)) / nb - sum(rng.choices(good, k=ng)) / ng
            for _ in range(DRAWS)]


def quantiles(draws: list[float]) -> tuple[float, float]:
    s = sorted(draws)
    return s[int(0.025 * DRAWS)], s[int(0.975 * DRAWS) - 1]


def wald_dj(bad: list[int], good: list[int]) -> tuple[float, float]:
    """Cross-check only: the normal-approximation interval for the same paired ΔJ."""
    def var_of_mean(xs: list[int]) -> float:
        mu = sum(xs) / len(xs)
        return (sum(x * x for x in xs) / len(xs) - mu * mu) / len(xs)
    dj = sum(bad) / len(bad) - sum(good) / len(good)
    se = math.sqrt(var_of_mean(bad) + var_of_mean(good))
    return dj - Z95 * se, dj + Z95 * se


def discordant(ds: list[int]) -> tuple[int, int]:
    """(A only, C only) — the pairs that carry signal."""
    return sum(d == -1 for d in ds), sum(d == 1 for d in ds)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Δ and ≥ survive a redirect on Windows too
    arms = {name: load(RESULTS / d / "details.jsonl") for name, d in ARMS.items()}
    print("== 1. guard — each summary.json reproduced from its details.jsonl (all graded rows)")
    ok = True
    for name, d in ARMS.items():
        summary = json.loads((RESULTS / d / "summary.json").read_text(encoding="utf-8"))
        ok &= guard(f"{name} {d:<8}", arms[name], summary)
    # And the paired interval must be the one RESULTS.md published for the pilot's arm C recall:
    # 25 caught only by C, 1 only by A, n=53 -> [+30.5, +48.4].
    pilot_ci = [round(x * 100, 1) for x in paired_ci(1, 25, 53)]
    print(f"  paired interval on the pilot's arm C recall: {pilot_ci} (published [30.5, 48.4]) "
          f"{'ok' if pilot_ci == [30.5, 48.4] else 'MISMATCH'}")
    ok &= pilot_ci == [30.5, 48.4]
    if not ok:
        sys.exit("GUARD FAILED — details.jsonl does not reproduce summary.json; nothing below is read")
    print("  GUARD PASSED")

    print("\n== 2. the in-sample mark")
    if not check_mark(arms["A"], arms["C"]):
        sys.exit("MARK FAILED — the in_pilot rows are not the pilot's graded items")
    for name in ARMS:
        hist = pilot_from_git(name)
        verdict = "git unavailable" if hist is None else str(
            {key(r) for r in hist} == {key(r) for r in arms[name] if r["in_pilot"]})
        print(f"  == the pilot's own arm {name} details @ {PILOT_HISTORY[name][0]}: {verdict}")

    parts = {name: split(rows) for name, rows in arms.items()}
    read_survival(parts)
    read_arms(parts)
    deltas = read_paired(parts)
    read_in_vs_out(deltas)
    read_retest(arms)
    read_verdicts(parts, deltas)


def read_survival(parts: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
    print("\n== 3. survival (rows whose diff could not be fetched are dropped, never re-drawn)")
    print(f"  {'':<14} {'planned':>8} {'graded':>7} {'dropped':>8} {'bad':>5} {'good':>5}")
    for sub, rows in parts["A"].items():
        r = rates(rows)
        print(f"  {sub:<14} {PLANNED[sub]:>8} {r['n_graded']:>7} {PLANNED[sub] - r['n_graded']:>8} "
              f"{r['bad']:>5} {r['good']:>5}")
    n_all = len(parts["A"]["all"])
    fires = n_all < MIN_SURVIVING
    print(f"  fewer than {MIN_SURVIVING} of {PLANNED['all']} surviving? {n_all} survived — "
          f"{'FIRES' if fires else 'does not fire'} "
          f"(out of sample alone: {len(parts['A']['out-of-sample'])})")


def read_arms(parts: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
    print("\n== 4. each arm, three ways (Wilson 95%)")
    head = (f"  {'':<14} {'arm':<4} {'recall':>7} {'(k/n)':>9} {'CI':>16}   {'false rej':>9} "
            f"{'(k/n)':>9} {'CI':>16}   {'reject':>6} {'J':>6}")
    print(head)
    for sub in PLANNED:
        for name in ARMS:
            r = rates(parts[name][sub])
            rlo, rhi = r["recall_ci"]
            flo, fhi = r["false_rej_ci"]
            rci = f"[{pct(rlo)}, {pct(rhi)}]"
            fci = f"[{pct(flo)}, {pct(fhi)}]"
            print(f"  {sub:<14} {name:<4} {pct(r['recall']):>7} {r['caught']:>4}/{r['bad']:<4} "
                  f"{rci:>16}   {pct(r['false_rej']):>9} {r['wrongly']:>4}/{r['good']:<4} "
                  f"{fci:>16}   {pct(r['reject_rate']):>6} {pp(r['j']):>6}")


def read_paired(parts: dict[str, dict[str, list[dict[str, Any]]]]
                ) -> dict[str, tuple[list[int], list[int], list[float]]]:
    print("\n== 5. C − A, paired over the same items")
    print("  ΔTPR and ΔFPR: chimera/eval/paired.py (McNemar, Wilson on the discordant pairs) — the")
    print("  method the earlier pre-registrations fixed. ΔJ: no paired method was specified for J;")
    print(f"  paired bootstrap over items within label, seed {SEED}, {DRAWS:,} draws, percentile 95%.")
    out: dict[str, tuple[list[int], list[int], list[float]]] = {}
    rng = random.Random(SEED)
    for sub in PLANNED:
        bad, good = paired_deltas(parts["A"][sub], parts["C"][sub])
        a_only_b, c_only_b = discordant(bad)
        a_only_g, c_only_g = discordant(good)
        tlo, thi = paired_ci(a_only_b, c_only_b, len(bad))
        flo, fhi = paired_ci(a_only_g, c_only_g, len(good))
        draws = bootstrap_dj(bad, good, rng)
        jlo, jhi = quantiles(draws)
        wlo, whi = wald_dj(bad, good)
        dtpr, dfpr = sum(bad) / len(bad), sum(good) / len(good)
        print(f"  {sub}")
        print(f"    ΔTPR {pp(dtpr)} pp  CI [{pp(tlo)}, {pp(thi)}]  discordant {a_only_b + c_only_b} "
              f"(C only {c_only_b}, A only {a_only_b})  n={len(bad)}")
        print(f"    ΔFPR {pp(dfpr)} pp  CI [{pp(flo)}, {pp(fhi)}]  discordant {a_only_g + c_only_g} "
              f"(C only {c_only_g}, A only {a_only_g})  n={len(good)}")
        print(f"    ΔJ   {pp(dtpr - dfpr)} pp  bootstrap CI [{pp(jlo)}, {pp(jhi)}]  "
              f"(Wald cross-check [{pp(wlo)}, {pp(whi)}])")
        net_bad, net_good = sum(bad), sum(good)
        print(f"    net: C catches {net_bad:+d} more incorrect comments and rejects {net_good:+d} "
              f"more correct ones — {net_good / net_bad:.2f} correct findings per extra catch")
        out[sub] = (bad, good, draws)
    return out


def read_in_vs_out(deltas: dict[str, tuple[list[int], list[int], list[float]]]) -> None:
    print("\n== 6. did C's edge shrink between in-sample and out-of-sample? (independent groups)")
    _, _, d_in = deltas["in-sample"]
    _, _, d_out = deltas["out-of-sample"]
    diff = [i - o for i, o in zip(d_in, d_out, strict=True)]
    lo, hi = quantiles(diff)
    b_in, g_in, _ = deltas["in-sample"]
    b_out, g_out, _ = deltas["out-of-sample"]
    point = (sum(b_in) / len(b_in) - sum(g_in) / len(g_in)) - (
        sum(b_out) / len(b_out) - sum(g_out) / len(g_out))
    print(f"  ΔJ(in) − ΔJ(out) = {pp(point)} pp  bootstrap CI [{pp(lo)}, {pp(hi)}]")


def read_retest(arms: dict[str, list[dict[str, Any]]]) -> None:
    print("\n== 7. the 105 in-sample items were graded twice — the pilot, then this run")
    print("  (not pre-registered; one re-run is one difference, not a variance)")
    for name in ARMS:
        pub = PILOT_PUBLISHED[name]
        now = rates([r for r in arms[name] if r["in_pilot"]])
        hist = pilot_from_git(name)
        j_then = pub["caught"] / pub["bad"] - pub["wrongly"] / pub["good"]
        print(f"  arm {name}: pilot recall {pub['caught']}/{pub['bad']} "
              f"({pct(pub['caught'] / pub['bad'])}), false rej {pub['wrongly']}/{pub['good']} "
              f"({pct(pub['wrongly'] / pub['good'])}), J {pp(j_then)} · this run recall "
              f"{now['caught']}/{now['bad']} ({pct(now['recall'])}), false rej "
              f"{now['wrongly']}/{now['good']} ({pct(now['false_rej'])}), J {pp(now['j'])} · "
              f"J moved {pp(now['j'] - j_then)} pp")
        if hist is None:
            print("    per-item agreement: git unavailable")
            continue
        before = {key(r): r["verdict"] for r in hist}
        pilot = rates(hist)
        assert (pilot["caught"], pilot["wrongly"]) == (pub["caught"], pub["wrongly"]), name
        same = sum(before[key(r)] == r["verdict"] for r in arms[name] if r["in_pilot"])
        print(f"    same verdict on {same}/{len(before)} items at temperature 0 "
              f"(git @ {PILOT_HISTORY[name][0]} reproduces the published counts)")
    pa, pc = PILOT_PUBLISHED["A"], PILOT_PUBLISHED["C"]
    pilot_dj = (pc["caught"] - pa["caught"]) / pa["bad"] - (pc["wrongly"] - pa["wrongly"]) / pa["good"]
    now_a = rates([r for r in arms["A"] if r["in_pilot"]])
    now_c = rates([r for r in arms["C"] if r["in_pilot"]])
    now_dj = now_c["j"] - now_a["j"]
    print(f"  C − A in J on the same 105: pilot {pp(pilot_dj)} pp · this run {pp(now_dj)} pp · "
          f"moved {pp(now_dj - pilot_dj)} pp with nothing changed but the run")


def band(value: float, lo: float, hi: float) -> str:
    if value < lo:
        return "BELOW the band"
    if value > hi:
        return "ABOVE the band"
    return "inside the band"


def first_rule(r: dict[str, Any]) -> str:
    """PREREGISTRATION.md's decision rule, as the runner applies it."""
    if r["recall"] >= 0.60 and r["false_rej"] <= CEILING:
        return "DISCRIMINATES"
    if r["recall"] >= 0.30 and r["false_rej"] <= CEILING:
        return "WEAK"
    return "DOES NOT DISCRIMINATE"


def read_verdicts(parts: dict[str, dict[str, list[dict[str, Any]]]],
                  deltas: dict[str, tuple[list[int], list[int], list[float]]]) -> None:
    a = rates(parts["A"]["out-of-sample"])
    c = rates(parts["C"]["out-of-sample"])
    bad, good, draws = deltas["out-of-sample"]
    dj = sum(bad) / len(bad) - sum(good) / len(good)
    jlo, jhi = quantiles(draws)
    print("\n== 8. the three primary comparisons, out of sample (PREREGISTRATION-full.md)")
    print(f"  1. A rejection recall   {pct(a['recall'])} ({a['caught']}/{a['bad']})  "
          f"Wilson [{pct(a['recall_ci'][0])}, {pct(a['recall_ci'][1])}]  "
          f"first rule: {first_rule(a)}")
    print(f"  2. C − A in J           {pp(dj)} pp  paired bootstrap [{pp(jlo)}, {pp(jhi)}]  "
          f"{'excludes 0' if jlo > 0 or jhi < 0 else 'includes 0'}")
    over = "ABOVE" if c["false_rej"] > CEILING else "within"
    lower = "above" if c["false_rej_ci"][0] > CEILING else "not above"
    print(f"  3. C false rejection    {pct(c['false_rej'])} ({c['wrongly']}/{c['good']})  "
          f"Wilson [{pct(c['false_rej_ci'][0])}, {pct(c['false_rej_ci'][1])}]  {over} the 20% "
          f"ceiling; lower bound {lower} it")

    print("\n== 9. predictions (out-of-sample bands, fixed before the run)")
    measured = {"A recall": a["recall"], "C recall": c["recall"],
                "C false rejection": c["false_rej"], "C - A in J": dj}
    for name, (lo, hi) in PREDICTED.items():
        value = measured[name]
        shown = pp(value) + " pp" if name == "C - A in J" else pct(value)
        span = (f"{pp(lo)} to {pp(hi)} pp" if name == "C - A in J"
                else f"{lo:.0%}–{hi:.0%}")
        print(f"  {name:<18} predicted {span:<16} measured {shown:<9} {band(value, lo, hi)}")

    print("\n== 10. uninformative conditions")
    for sub in PLANNED:
        for name in ARMS:
            r = rates(parts[name][sub])
            print(f"  {sub:<14} {name}: unparsed {r['unparsed']}/{r['n']} · call failed "
                  f"{r['call_failed']}/{r['n']} · reject rate {pct(r['reject_rate'])}")
        b, g, _ = deltas[sub]
        mb, mg = sum(discordant(b)), sum(discordant(g))
        print(f"  {sub:<14} discordant pairs: recall {mb}, false rejection {mg} "
              f"({'both' if min(mb, mg) >= 10 else 'NOT both'} ≥ 10)")


if __name__ == "__main__":
    main()
