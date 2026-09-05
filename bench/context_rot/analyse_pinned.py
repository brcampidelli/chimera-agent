"""Read the pinned run's rows and print every table in `RESULTS_pinned.md`.

    python bench/context_rot/analyse_pinned.py

Separate from `run.py` on purpose: the run costs money and the analysis does not, so re-reading a
conclusion must never risk re-spending. Everything here is a pure function of the committed JSON.

Two rules from the pre-registration are enforced here rather than left to whoever reads the output:

* A row whose answering backend differs from the one asked for is **dropped and counted**, never
  averaged in. That row means `allow_fallbacks` did not hold and the arm is contaminated.
* An error is **not a failure**. A rate-limit, a refusal and a wrong answer are three different
  things, and folding them together is how a rate stops meaning anything. A backend that cannot
  produce the pre-registered minimum of usable rows is reported ABSENT, with its reason.
"""

from __future__ import annotations

import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from scipy.stats import fisher_exact

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

HERE = Path(__file__).resolve().parent

#: Published input price per million tokens, read from the endpoints API on 2026-09-05. Used only to
#: report what was spent; nothing in any verdict depends on it.
PRICE_PER_M = {
    "Baidu": 0.050, "OpenInference": 0.050, "Relace": 0.065, "Sail Research": 0.065,
    "AkashML": 0.065, "DigitalOcean": 0.080, "DeepInfra": 0.080, "Wafer": 0.100,
    "Together": 0.140,
}

#: Pre-registered minimum usable rows before a backend's rate is read at all.
MIN_ROWS = 8


def load(path: str | Path) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Return (all, usable, errored, mis-routed) for one rows file."""
    rows: list[dict] = json.loads(Path(path).read_text(encoding="utf-8"))
    errored = [r for r in rows if r.get("error")]
    routed_wrong = [
        r for r in rows if not r.get("error") and r.get("pinned") and r["provider"] != r["pinned"]
    ]
    usable = [
        r for r in rows if not r.get("error") and (not r.get("pinned") or r["provider"] == r["pinned"])
    ]
    return rows, usable, errored, routed_wrong


def rate(rows: list[dict], key: str) -> tuple[int, int]:
    return sum(bool(r[key]) for r in rows), len(rows)


def cost(rows: list[dict]) -> float:
    return sum(r["prompt_tokens"] * PRICE_PER_M.get(r.get("pinned") or "", 0.065) for r in rows) / 1e6


def phase_a() -> tuple[int, int, float]:
    print("## Phase A — does a pinned cell reproduce")
    spent = 0.0
    cells: list[tuple[int, int]] = []
    for tag, name in [("batch 1", "rows_pinned_A1.json"), ("batch 2", "rows_pinned_A2.json")]:
        path = HERE / name
        if not path.exists():
            print(f"  {tag}: missing")
            continue
        _, ok, err, wrong = load(path)
        spent += cost(ok)
        r, n = rate(ok, "rule")
        f, _ = rate(ok, "fact")
        toks = {x["prompt_tokens"] for x in ok}
        cells.append((r, n))
        print(f"  {tag}: n={n}  RULE {r}/{n}  FACT {f}/{n}  errors={len(err)}  mis-routed={len(wrong)}"
              f"  tokens={toks}")
    if len(cells) == 2:
        (r1, n1), (r2, n2) = cells
        _, p = fisher_exact([[r1, n1 - r1], [r2, n2 - r2]])
        verdict = "STOP — pinning is not enough" if p < 0.05 else "PROCEED"
        print(f"  Fisher between batches: p = {p:.4f}  ->  {verdict}")
    total = sum(r for r, _ in cells), sum(n for _, n in cells)
    return total[0], total[1], spent


def phase_b() -> float:
    print("\n## Phase B — the ladder, pinned to Baidu")
    path = HERE / "rows_pinned_B.json"
    if not path.exists():
        print("  missing")
        return 0.0
    _, ok, err, wrong = load(path)
    by_len: dict[int, list[dict]] = defaultdict(list)
    for r in ok:
        by_len[r["target_tokens"]].append(r)
    print(f"  {'target':>8}{'realised':>10}{'n':>4}{'RULE':>9}{'FACT':>9}")
    shortest = min(by_len)
    r0, n0 = rate(by_len[shortest], "rule")
    for tgt in sorted(by_len):
        rs = by_len[tgt]
        real = round(sum(x["prompt_tokens"] for x in rs) / len(rs))
        r, n = rate(rs, "rule")
        f, _ = rate(rs, "fact")
        note = ""
        if tgt != shortest:
            _, p = fisher_exact([[r0, n0 - r0], [r, n - r]])
            drop = (r0 / n0 - r / n) * 100
            note = f"   drop vs {shortest // 1000}k = {drop:5.1f} pp, p = {p:.4f}"
            if drop >= 30 and p < 0.05:
                note += "   <= KNEE"
        print(f"  {tgt:>8}{real:>10}{len(rs):>4}{r:>5}/{n:<3}{f:>5}/{n:<3}{note}")
    print(f"  errors={len(err)}  mis-routed={len(wrong)}")
    return cost(ok)


def phase_c(base_rule: int, base_n: int) -> float:
    print("\n## Phase C — eight backends at 792k, against Baidu's own rate")
    spent = 0.0
    for path in sorted(glob.glob(str(HERE / "rows_pinned_C_*.json"))):
        if path.endswith("_ladder.json"):
            continue
        rows, ok, err, wrong = load(path)
        spent += cost(rows)
        pin = rows[0].get("pinned") or "?"
        if len(ok) < MIN_ROWS:
            why = (err[0]["error"][:60] if err else "no reason recorded")
            print(f"  {pin:<16} ABSENT — {len(ok)} usable rows (<{MIN_ROWS}); {why}")
            continue
        r, n = rate(ok, "rule")
        f, _ = rate(ok, "fact")
        _, p = fisher_exact([[base_rule, base_n - base_rule], [r, n - r]])
        flag = "   <= FAILS" if r < n and p < 0.05 else ""
        print(f"  {pin:<16} n={n:<3} RULE {r}/{n:<3} FACT {f}/{n:<3}"
              f" errors={len(err)} mis-routed={len(wrong)}  p={p:.4f}{flag}")
    return spent


def broken_backend_ladder() -> float:
    """A failing backend across lengths: a broken machine looks the same as a knee at one length."""
    paths = sorted(glob.glob(str(HERE / "rows_pinned_C_*_ladder.json")))
    if not paths:
        return 0.0
    print("\n## The failing backend, across lengths")
    spent = 0.0
    for path in paths:
        rows, ok, err, wrong = load(path)
        spent += cost(rows)
        pin = rows[0].get("pinned") or "?"
        by_len: dict[int, list[dict]] = defaultdict(list)
        for r in ok:
            by_len[r["target_tokens"]].append(r)
        print(f"  {pin}:  errors={len(err)}  mis-routed={len(wrong)}")
        for tgt in sorted(by_len):
            rs = by_len[tgt]
            r, n = rate(rs, "rule")
            f, _ = rate(rs, "fact")
            real = round(sum(x["prompt_tokens"] for x in rs) / len(rs))
            print(f"    {tgt:>8} (real {real:>7})  n={n:<3} RULE {r}/{n:<3} FACT {f}/{n}")
    return spent


def broken_rule_breakdown() -> None:
    """WHICH rule broke, per file. An aggregate that hides this is the mistake, not the summary."""
    print("\n## Which rule broke, where")
    for path in sorted(glob.glob(str(HERE / "rows_pinned_*.json"))):
        _, ok, _, _ = load(path)
        counts: Counter[str] = Counter()
        for r in ok:
            if r["rule"]:
                continue
            for flag, held in (r.get("detail") or {}).items():
                if not held:
                    counts[flag] += 1
        if counts:
            print(f"  {Path(path).name}: {dict(counts)}")


def main() -> int:
    rule, n, a = phase_a()
    b = phase_b()
    c = phase_c(rule, n)
    d = broken_backend_ladder()
    broken_rule_breakdown()
    print(f"\n  measured spend: US$ {a + b + c + d:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
