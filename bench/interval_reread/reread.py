"""Re-read the published intervals under PROTOCOL §11 — see PREREGISTRATION.md (committed first).

    uv run python bench/interval_reread/reread.py > bench/interval_reread/results/reread.txt
    uv run python bench/interval_reread/reread.py --json <elsewhere.json>   # leaves results/ alone

Offline, US$ 0, deterministic. For every verdict it first REPRODUCES the published interval with the
published method from the same data (§2aa) and only then prints the closed-form interval from
`chimera/eval/proportions.py`. The retired methods appear here only inside the reproduction step,
under names that say so.
"""

from __future__ import annotations

import io
import json
import os
import random
import re
import statistics
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench" / "harness_bench"))

from chimera.eval import proportions as P  # noqa: E402

OUT = Path(__file__).resolve().parent / "results" / "reread.json"
Z_COPY = 1.959964  # the z the H7 copy hard-coded; used only to reproduce what it printed

record: list[dict[str, Any]] = []


def pct(x: float) -> str:
    return f"{x * 100:+.1f}"


def iv(low: float, high: float, fmt: Callable[[float], str] = lambda v: f"{v:+.3f}") -> str:
    return f"[{fmt(low)}, {fmt(high)}]"


def check(label: str, got: Sequence[float], published: Sequence[float], places: int) -> bool:
    same = all(round(g, places) == round(p, places) for g, p in zip(got, published, strict=True))
    shown = ", ".join(f"{g:.{places}f}" for g in got)
    print(f"    reproduce {label}: [{shown}] vs published {list(published)} -> {'ok' if same else 'NOT REPRODUCED'}")
    return same


def note(bench: str, verdict: str, criterion: float, old: tuple[float, float], new: tuple[float, float],
         method: str, reproduced: bool, side: str = "low") -> None:
    """Record one re-read. `side` is the bound the verdict was read on ('low', 'high' or 'both')."""

    def holds(interval: tuple[float, float]) -> bool:
        low, high = interval
        if side == "low":
            return low > criterion
        if side == "high":
            return high < criterion
        return low > criterion or high < criterion  # 'both': the interval excludes the criterion

    crossed = reproduced and holds(old) != holds(new)
    record.append({
        "bench": bench, "verdict": verdict, "criterion": criterion, "side": side,
        "published": [round(old[0], 4), round(old[1], 4)], "reread": [round(new[0], 4), round(new[1], 4)],
        "method": method, "reproduced": reproduced, "crosses_criterion": crossed,
    })
    flag = "  <-- CROSSES ITS CRITERION" if crossed else ""
    print(f"    re-read  {iv(*new)}  ({method}){flag}")


# --------------------------------------------------------------------------------------------------
# A1. harness_bench


SE_CLASS = "Software Engineering & Codebase Maintenance"
#: `task.yaml: class` of the 23 tasks, read from ~/harness-bench/tasks/<task>/task.yaml on 2026-10-05.
NOT_SE = {
    "022-local-rest-api-summary", "080-schema-roundtrip-conversion",  # Workspace, Tool Use & Multimodal
    "051-sql-query-report", "089-ab-test-caveat-analysis", "092-schema-drift-audit",
    "094-metric-definition-migration-diff",  # Data, BI & Finance Analytics
    "064-service-dependency-triage",  # SRE, DevOps & Release Ops
}


def harness_grid(corrected: bool) -> dict[tuple[str, tuple[int, int, int], int], float]:
    from effects_with_087_corrected import REGRADED_087

    grid = {}
    path = ROOT / "bench" / "harness_bench" / "results" / "2026-09-13-factorial.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        bits = (row["repo_map"], row["checklist"], row["planner"])
        value = row["outcome"]
        hid = f"arm-{row['arm']}-r{row['replica']}"
        if corrected and row["task"] == "087-cli-parser-bug-tests" and hid in REGRADED_087:
            value = REGRADED_087[hid]
        if value is not None:
            grid[(row["task"], bits, row["replica"])] = float(value)
    return grid


def published_main_bootstrap(xs: list[float], n: int = 10000, seed: int = 20260912) -> tuple[float, float]:
    """`read_results.py`'s percentile bootstrap, reproduced verbatim (retired by §11)."""
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choice(xs) for _ in xs) for _ in range(n))
    return means[int(n * 0.025)], means[int(n * 0.975)]


def reread_harness() -> None:
    from effects_by_partition import (
        INTERACTIONS_READ,
        SEED,
        bare_means,
        boot_diff,
        boot_mean,
        per_task_deltas,
    )
    from effects_with_087_corrected import TASKS

    print("\n== A1. harness_bench — main effects (RESULTS.md table; 087 uncorrected, as published there)")
    deltas_raw = per_task_deltas(harness_grid(corrected=False))
    published = {"A repo-map": (-0.036, 0.010), "B checklist": (-0.052, 0.052), "C planner": (-0.022, 0.029)}
    #: The same verdict published a second time, in DEAD-TASKS.md's "published" column. The two
    #: publications disagree on C's lower bound (-0.022 in RESULTS.md, -0.023 here).
    published_again = {"A repo-map": (-0.036, 0.010), "B checklist": (-0.052, 0.052), "C planner": (-0.023, 0.029)}
    for factor, by_task in deltas_raw.items():
        xs = [by_task[t] for t in TASKS if t in by_task]
        old = published_main_bootstrap(xs)
        print(f"  {factor}  Δ={statistics.fmean(xs):+.3f}  n={len(xs)}")
        ok_results = check("bootstrap, RESULTS.md", old, published[factor], 3)
        ok = check("bootstrap, DEAD-TASKS.md", old, published_again[factor], 3)
        if not ok_results and ok:
            print("    -> RESULTS.md's figure is not what the data give; DEAD-TASKS.md's is. Read against the latter.")
        mean, low, high = P.mean_t_interval(xs)
        note("harness_bench", f"main effect {factor} spans 0", 0.0, published[factor], (low, high),
             "t on per-task deltas", ok, side="both")
        _, l90, h90 = P.mean_t_interval(xs, alpha=0.10)
        print(f"    smallest symmetric margin the 90% t interval fits inside: ±{max(abs(l90), abs(h90)):.3f}"
              "  (descriptive, §12: no margin was declared)")

    grid = harness_grid(corrected=True)
    deltas = per_task_deltas(grid)
    bare = bare_means(grid)
    ordered = sorted(TASKS, key=lambda t: bare[t])
    partitions = {
        "FAMILY": {"SE": [t for t in TASKS if t not in NOT_SE], "other": [t for t in TASKS if t in NOT_SE]},
        "TERCILE": {"bottom": ordered[:8], "middle": ordered[8:16], "top": ordered[16:]},
    }
    published_inter = {
        ("FAMILY", "A repo-map"): ((-0.052, 0.029), (-0.067, 0.042)),
        ("FAMILY", "B checklist"): ((-0.084, 0.074), (-0.114, 0.097)),
        ("FAMILY", "C planner"): ((-0.001, 0.096), (-0.014, 0.114)),
        ("TERCILE", "A repo-map"): ((-0.040, 0.038), (-0.047, 0.053)),
        ("TERCILE", "B checklist"): ((0.002, 0.122), (-0.013, 0.144)),
        ("TERCILE", "C planner"): ((-0.041, 0.074), (-0.062, 0.094)),
    }
    for name, strata in partitions.items():
        labels = list(strata)
        first, last = labels[0], labels[-1]
        print(f"\n== A1. harness_bench — {name} partition (087 corrected, seed {SEED}), interaction {first} − {last}")
        for factor, by_task in deltas.items():
            rng = random.Random(SEED)
            groups = {}
            for label in labels:
                xs = [by_task[t] for t in strata[label] if t in by_task]
                boot_mean(xs, rng)  # advances the registered generator exactly as the reader did
                groups[label] = xs
            old95 = boot_diff(groups[first], groups[last], rng)
            old_bf = boot_diff(groups[first], groups[last], random.Random(SEED), alpha=0.05 / INTERACTIONS_READ)
            pub95, pub_bf = published_inter[(name, factor)]
            print(f"  {factor}")
            ok = check("95% bootstrap", old95, pub95, 3) & check("Bonferroni bootstrap", old_bf, pub_bf, 3)
            for label in labels:
                mean, low, high = P.mean_t_interval(groups[label])
                print(f"    stratum {label:<7} n={len(groups[label]):<2} {mean:+.3f}  t {iv(low, high)}")
            diff, low, high = P.welch_t_interval(groups[first], groups[last])
            print(f"    interaction {diff:+.3f}")
            note("harness_bench", f"{name} × {factor} interaction (95%)", 0.0, pub95, (low, high),
                 "Welch t, 95%", ok, side="both")
            _, blow, bhigh = P.welch_t_interval(groups[first], groups[last], alpha=0.05 / INTERACTIONS_READ)
            note("harness_bench", f"{name} × {factor} interaction (Bonferroni over 6)", 0.0, pub_bf,
                 (blow, bhigh), "Welch t, 1 − 0.05/6", ok, side="both")


# --------------------------------------------------------------------------------------------------
# A2/C. chat_history


def reread_chat_history() -> None:
    print("\n== A2. chat_history — primary REAL − FLAT, non-inferiority margin −10 pp (registered)")
    run = json.loads((ROOT / "bench" / "chat_history" / "results" / "run.json").read_text(encoding="utf-8"))
    by_task: dict[str, list[int]] = {}
    both = flat_only = real_only = neither = 0
    for row in run["rows"]:
        runs = row["runs"]
        if "FLAT" not in runs or "REAL" not in runs or runs["FLAT"]["halt"] or runs["REAL"]["halt"]:
            continue
        a, b = bool(runs["FLAT"]["graded"]["passed"]), bool(runs["REAL"]["graded"]["passed"])
        by_task.setdefault(row["task"], []).append(int(b) - int(a))
        both += a and b
        flat_only += a and not b
        real_only += b and not a
        neither += not a and not b
    diffs = [sum(v) / len(v) for v in by_task.values()]
    rng = random.Random(25)
    draws = 20_000
    means = sorted(sum(rng.choice(diffs) for _ in range(len(diffs))) / len(diffs) for _ in range(draws))
    old = (means[int(0.025 * draws)], means[int(0.975 * draws) - 1])
    print(f"  per-task mean {statistics.fmean(diffs):+.3f} over {len(diffs)} tasks")
    ok = check("cluster bootstrap", old, (-0.102, 0.016), 3)
    _, low, high = P.mean_t_interval(diffs)
    note("chat_history", "REAL non-inferior to FLAT (lower >= -0.10)", -0.10, old, (low, high),
         "t on per-task differences", ok, side="low")

    print(f"\n== C. chat_history — the paired line (n={both + flat_only + real_only + neither}: FLAT-only "
          f"{flat_only}, REAL-only {real_only})")
    old_n = P.newcombe_paired(both, flat_only, real_only, neither, Z_COPY, phi_correction=False)
    ok = check("Newcombe as H7 printed it (uncorrected phi)", old_n, (-0.103, 0.022), 3)
    new_n = P.newcombe_paired(both, flat_only, real_only, neither)
    note("chat_history", "paired REAL − FLAT spans 0 (descriptive)", 0.0, old_n, new_n, "Newcombe method 10", ok,
         side="both")
    bp = P.bonett_price_paired(flat_only, real_only, both + flat_only + real_only + neither)
    print(f"    for the record, Bonett-Price {iv(*bp)}")


# --------------------------------------------------------------------------------------------------
# A3/B. review_judge


def reread_review_judge() -> None:
    print("\n== A3. review_judge — the reader run fresh, compared to results/full_read.txt")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}  # the reader prints Δ and −
    fresh = subprocess.run([sys.executable, str(ROOT / "bench" / "review_judge" / "read_full.py")],
                           capture_output=True, text=True, cwd=ROOT, check=False, encoding="utf-8", env=env)
    published = (ROOT / "bench" / "review_judge" / "results" / "full_read.txt").read_text(encoding="utf-8")
    same = fresh.returncode == 0 and fresh.stdout.strip() == published.strip()
    print(f"    reproduce read_full.py output byte for byte: {'ok' if same else 'NOT REPRODUCED'}")
    block = re.compile(
        r"^  (all|in-sample|out-of-sample)\n"
        r"    ΔTPR (\S+) pp  CI \[(\S+), (\S+)\]  discordant \d+ \(C only (\d+), A only (\d+)\)  n=(\d+)\n"
        r"    ΔFPR (\S+) pp  CI \[(\S+), (\S+)\]  discordant \d+ \(C only (\d+), A only (\d+)\)  n=(\d+)\n"
        r"    ΔJ   (\S+) pp  bootstrap CI \[(\S+), (\S+)\]", re.M)
    js: dict[str, tuple[float, float, float]] = {}
    for m in block.finditer(published):
        part = m.group(1)
        num = [float(x.replace("−", "-")) / 100 for x in (m.group(2), m.group(3), m.group(4))]
        tpr_c, tpr_a, tpr_n = int(m.group(5)), int(m.group(6)), int(m.group(7))
        fpr_pub = [float(x.replace("−", "-")) / 100 for x in (m.group(8), m.group(9), m.group(10))]
        fpr_c, fpr_a, fpr_n = int(m.group(11)), int(m.group(12)), int(m.group(13))
        j_pub = [float(x.replace("−", "-")) / 100 for x in (m.group(14), m.group(15), m.group(16))]
        print(f"  {part}")
        old_t = P.conditional_wilson_paired(tpr_a, tpr_c, tpr_n)
        ok_t = check("ΔTPR (conditional)", [v * 100 for v in old_t], [v * 100 for v in num[1:]], 1)
        new_t = P.bonett_price_paired(tpr_a, tpr_c, tpr_n)
        note("review_judge", f"{part} ΔTPR excludes 0", 0.0, old_t, new_t, "Bonett-Price", ok_t and same, side="both")
        old_f = P.conditional_wilson_paired(fpr_a, fpr_c, fpr_n)
        ok_f = check("ΔFPR (conditional)", [v * 100 for v in old_f], [v * 100 for v in fpr_pub[1:]], 1)
        new_f = P.bonett_price_paired(fpr_a, fpr_c, fpr_n)
        note("review_judge", f"{part} ΔFPR excludes 0", 0.0, old_f, new_f, "Bonett-Price", ok_f and same, side="both")
        d_t, d_f = (tpr_c - tpr_a) / tpr_n, (fpr_c - fpr_a) / fpr_n
        dj, jlo, jhi = P.mover_difference(d_t, new_t, d_f, new_f)
        print(f"    ΔJ {pct(dj)} pp; published bootstrap {iv(j_pub[1], j_pub[2], pct)}")
        note("review_judge", f"{part} C − A in J excludes 0", 0.0, (j_pub[1], j_pub[2]), (jlo, jhi),
             "MOVER over two Bonett-Price intervals", same, side="both")
        js[part] = (dj, jlo, jhi)
    if "in-sample" in js and "out-of-sample" in js:
        a, b = js["in-sample"], js["out-of-sample"]
        diff, low, high = P.mover_difference(a[0], (a[1], a[2]), b[0], (b[1], b[2]))
        print(f"  ΔJ(in) − ΔJ(out) {pct(diff)} pp")
        note("review_judge", "C's edge shrank in vs out of sample", 0.0, (-0.176, 0.240), (low, high),
             "MOVER over the two ΔJ intervals", same, side="both")


#: Section B's inputs, frozen: the 10 files holding the 22 `PairedResult.summary()` the
#: pre-registration counted on 2026-10-05. They were found then with
#: `git grep -l '"discordant"' -- 'bench/**/*.json'`, but the reader does not search again: a bench
#: committed after the re-read writes a Bonett-Price `diff_ci`, which the reproduction step would
#: report as NOT REPRODUCED, and the re-read's record would move under a correct commit that never
#: touched it. A search also needs a git checkout; reading the files does not.
PAIRED_SUMMARY_FILES = (
    "bench/hierarchy/results/paired.json",
    "bench/hierarchy_multistep/results/paired.json",
    "bench/learning_lift/results_hard_paired/learning.json",
    "bench/learning_lift/results_hard_semantic/learning.json",
    "bench/learning_lift/results_recurring/learning.json",
    "bench/learning_lift/results_recurring_hard/learning.json",
    "bench/local_lift/_reverify_6/paired.json",
    "bench/local_lift/_reverify_n100/paired.json",
    "bench/memory_graph/results/graph_ab.json",
    "bench/retry_lift/results/retry.json",
)


def paired_summaries(root: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """``(file, json path, summary)`` for every `PairedResult.summary()` in the frozen inputs."""

    def walk(obj: Any, path: str, out: list[tuple[str, dict[str, Any]]]) -> None:
        if isinstance(obj, dict):
            if isinstance(obj.get("discordant"), dict) and "diff_ci" in obj:
                out.append((path, obj))
            for key, value in obj.items():
                walk(value, f"{path}/{key}", out)
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                walk(value, f"{path}[{i}]", out)

    summaries: list[tuple[str, str, dict[str, Any]]] = []
    for name in PAIRED_SUMMARY_FILES:
        found: list[tuple[str, dict[str, Any]]] = []
        walk(json.loads((root / name).read_text(encoding="utf-8")), "", found)
        summaries.extend((name, path, s) for path, s in found)
    return summaries


def reread_paired_summaries() -> None:
    print("\n== B. every committed PairedResult.summary() — the conditional interval re-read with Bonett-Price")
    for name, path, s in paired_summaries(ROOT):
        b, c, n = s["discordant"]["baseline_only"], s["discordant"]["treatment_only"], s["n"]
        print(f"  {name}{path}  n={n} baseline-only {b} treatment-only {c}  Δ={s['delta']:+.4f}")
        old = P.conditional_wilson_paired(b, c, n)
        ok = check("conditional", old, s["diff_ci"], 4)
        note(name, f"{path} significant={s['significant']}", 0.0, (s["diff_ci"][0], s["diff_ci"][1]),
             P.bonett_price_paired(b, c, n), f"Bonett-Price; exact McNemar p={P.mcnemar_exact(b, c):.3g}",
             ok, side="both")


# --------------------------------------------------------------------------------------------------
# A4-A6, C


def reread_edit_tools() -> None:
    print("\n== A4. edit_tools — median of B − A over 22 pairs (registered: median <= -2 and upper < 0)")
    data = json.loads((ROOT / "bench" / "edit_tools" / "results" / "ab.json").read_text(encoding="utf-8"))
    for key, metric in (("d_edits", "edit_calls"), ("d_tokens", "completion_tokens")):
        values = [p[key] for p in data["pair_rows"]]
        rng = random.Random(20260817)
        meds = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(10000))
        old = (meds[250], meds[9750])
        print(f"  {metric}: median {statistics.median(values):+}")
        ok = check("bootstrap of the median", old, data[metric]["ci95_median"], 1)
        _, low, high = P.median_interval(values)
        note("edit_tools", f"{metric} median CI excludes 0 (upper < 0)", 0.0, old, (low, high),
             "binomial order statistics", ok, side="high" if key == "d_edits" else "both")


def reread_auroc() -> None:
    from bench.jev_decisions.report import auroc

    print("\n== A5. wake_gate — AUROC wake vs sleep (the verdict is read on suppression, not on this interval)")
    rows = [json.loads(x) for x in (ROOT / "bench" / "wake_gate" / "results" / "wake_gate.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]
    scored = [(r["p_wake"], 1 if r["label"] == "wake" else 0) for r in rows
              if r.get("asked") and r.get("p_wake") is not None and r["label"] in ("wake", "not_yet", "unrelated")]
    point = auroc(scored) or 0.0
    rng = random.Random(7)
    draws = []
    for _ in range(2000):
        sample = [scored[rng.randrange(len(scored))] for _ in scored]
        a = auroc(sample)
        if a is not None:
            draws.append(a)
    draws.sort()
    old = (draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1])
    summary = json.loads((ROOT / "bench" / "wake_gate" / "results" / "summary.json").read_text(encoding="utf-8"))
    pub = summary["auroc_wake_vs_sleep"]
    n_pos = sum(y for _, y in scored)
    print(f"  AUROC {point:.3f} on {n_pos} wake / {len(scored) - n_pos} sleep")
    ok = check("bootstrap", (point, *old), pub, 3)
    note("wake_gate", "AUROC inside the predicted band 0.70-0.85 (point)", 0.5, old,
         P.auroc_hanley_mcneil(point, n_pos, len(scored) - n_pos), "Hanley-McNeil", ok, side="low")

    print("\n== A6. stop_gate — ΔAUROC(A3 − A1); the three arms' scores are identical")
    abl = json.loads((ROOT / "bench" / "stop_gate" / "results" / "ablation.json").read_text(encoding="utf-8"))
    same = abl["A1"]["auroc"] == abl["A3"]["auroc"] == abl["A2"]["auroc"]
    print(f"    arms identical: {same}; published Δ {abl['delta_A3_minus_A1']} — any paired method gives 0")
    positives = abl["positives"]
    negatives = abl["reached_the_model"] - positives
    delta = abl["delta_A3_minus_A1"]
    note("stop_gate", "ΔAUROC >= +0.08 with lower > +0.02", 0.02, (delta[1], delta[2]),
         (0.0, 0.0), "identical arms: Δ = 0 by construction", same, side="low")
    print(f"    per-arm AUROC Hanley-McNeil {iv(*P.auroc_hanley_mcneil(abl['A1']['auroc'][0], positives, negatives))}"
          f" (published bootstrap {abl['A1']['auroc'][1]:.3f}–{abl['A1']['auroc'][2]:.3f})")


def reread_newcombe_copies() -> None:
    print("\n== C. the Newcombe copies without the phi correction")
    cases = [
        ("sharded_recap", "pilot gate F − A (stops under +10 pp)", (27, 2, 3, 0), (-0.125, 0.190)),
        ("spoken_standard", "speakable B − A", (17, 11, 4, 40), (-0.197, 0.006)),
        ("spoken_standard", "format-speakable B − A", (23, 19, 2, 28), (-0.340, -0.121)),
    ]
    for bench, verdict, (both, base_only, treat_only, neither), pub in cases:
        print(f"  {bench}: {verdict}  table (both {both}, baseline-only {base_only}, treatment-only {treat_only},"
              f" neither {neither})")
        old = P.newcombe_paired(both, base_only, treat_only, neither, Z_COPY, phi_correction=False)
        ok = check("uncorrected phi", old, pub, 3)
        new = P.newcombe_paired(both, base_only, treat_only, neither)
        note(bench, verdict, 0.0, old, new, "Newcombe method 10", ok, side="both")
        n = both + base_only + treat_only + neither
        print(f"    for the record, Bonett-Price {iv(*P.bonett_price_paired(base_only, treat_only, n))}")


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # the report prints Δ and −; a cp1252 console cannot
    out = Path(sys.argv[sys.argv.index("--json") + 1]) if "--json" in sys.argv else OUT
    reread_harness()
    reread_chat_history()
    reread_review_judge()
    reread_paired_summaries()
    reread_edit_tools()
    reread_auroc()
    reread_newcombe_copies()
    print("\n== Summary")
    not_reproduced = [r for r in record if not r["reproduced"]]
    crossed = [r for r in record if r["crosses_criterion"]]
    print(f"  re-read {len(record)} intervals; not reproduced {len(not_reproduced)}; crossing their criterion "
          f"{len(crossed)}")
    for r in crossed:
        print(f"    {r['bench']}: {r['verdict']}  published {r['published']} -> {r['reread']} ({r['method']})")
    for r in not_reproduced:
        print(f"    NOT REPRODUCED {r['bench']}: {r['verdict']}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
