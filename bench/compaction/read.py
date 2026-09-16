"""Read `run.py`'s JSONL against the registered decision rule; print every summary for the hand count.

    python bench/compaction/read.py bench/compaction/results-2026-09-15.jsonl

Primary: the paired difference in "the final file honours the convention", note (A) against
note+rules (B), over the same conversations — `chimera/eval/paired.py` for the Wilson interval on
the discordant pairs and an exact McNemar (two-sided binomial on the discordant pairs) for the
registered p. A pair in which a compaction did not fire in BOTH arms is void and named.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from math import comb
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.eval.paired import compare_paired  # noqa: E402


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar on the discordant counts (b baseline-only, c treatment-only)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / 2**n
    return min(1.0, 2 * tail)


def main(path: str) -> int:
    rows = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    by_pair: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        by_pair[r["pair_id"]][r["arm"]] = r
    complete = {k: v for k, v in by_pair.items() if "note" in v and "rules" in v}
    void = [k for k, v in complete.items()
            if not (v["note"]["compacted"] and v["rules"]["compacted"]) or v["note"].get("halted") or v["rules"].get("halted")]
    valid = {k: v for k, v in complete.items() if k not in void}
    print(f"rows {len(rows)}  pairs complete {len(complete)}  void (no compaction in both arms, or halted) {len(void)}  valid {len(valid)}")
    for k in void:
        print(f"  void: {k}")

    a = [v["note"]["honoured"] for v in valid.values()]
    b = [v["rules"]["honoured"] for v in valid.values()]
    res = compare_paired(a, b, baseline_name="note", treatment_name="note+rules")
    p = mcnemar_exact(res.baseline_only, res.treatment_only)
    lo, hi = res.diff_ci
    print("\n== primary — final file honours the convention, paired ==")
    print(f"  A note        {sum(a)}/{len(a)} = {res.baseline_rate:.3f}")
    print(f"  B note+rules  {sum(b)}/{len(b)} = {res.treatment_rate:.3f}")
    print(f"  delta {res.delta:+.3f}  diff CI [{lo:+.3f}, {hi:+.3f}]  discordant B-only {res.treatment_only} / A-only {res.baseline_only}"
          f"  exact McNemar p = {p:.4g}")
    delta_pp = res.delta * 100
    if res.treatment_rate < res.baseline_rate:
        verdict = "REJECT AND REPORT LOUDLY — B < A"
    elif delta_pp >= 15 and p < 0.05:
        verdict = "ADOPT — delta ≥ +15 pp and p < 0.05"
    else:
        verdict = "REJECT — the registered bar (≥ +15 pp and p < 0.05) is not met"
    print(f"  DECISION (registered): {verdict}")

    print("\n== per pair ==")
    for k, v in sorted(valid.items()):
        print(f"  {k:<34} note={'✓' if v['note']['honoured'] else '✗'}  rules={'✓' if v['rules']['honoured'] else '✗'}"
              f"  files={'✓' if v['note']['file_written'] else '✗'}/{'✓' if v['rules']['file_written'] else '✗'}")
    lost = [k for k, v in valid.items() if v["note"]["honoured"] and not v["rules"]["honoured"]]
    print(f"\n  the other direction (A honoured, B did not): {lost or 'none'}")

    print("\n== cost ==")
    for arm in ("note", "rules"):
        rs = [r for r in rows if r["arm"] == arm]
        usd = sum(r["usd"] for r in rs)
        secs = sum(r["seconds"] for r in rs)
        print(f"  {arm:6} conversations {len(rs)}  US$ {usd:.4f}  mean US$ {usd / len(rs):.5f}  mean {secs / len(rs):.0f}s")
    routes = Counter(x for r in rows for x in r.get("routes", []))
    print(f"  routes per turn: {dict(routes)}")

    print("\n== every treatment summary, for the fabrication count (read against the convention) ==")
    for k, v in sorted(valid.items()):
        for s in v["rules"]["summaries"]:
            standing = s.split("Standing from that span:", 1)
            body = standing[1].strip() if len(standing) == 2 else "(note only — the summariser returned nothing standing)"
            print(f"  --- {k}\n      {body[:600]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
