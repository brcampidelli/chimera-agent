"""Read `runs.jsonl` and apply PREREGISTRATION.md §5 exactly as written. No run, no spend.

    python bench/proportionality/report.py <dir>/runs.jsonl
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def _pairs(rows: list[dict], family: str) -> dict[str, list[tuple[dict, dict]]]:
    """{item id: [(off, on), ...]} for the pairs where both arms ran to a verdict (no harness error)."""
    by: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for r in rows:
        if r["family"] == family:
            by[(r["id"], r["rep"])][r["arm"]] = r
    out: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for (item, _rep), arms in sorted(by.items()):
        if "off" in arms and "on" in arms and not arms["off"]["error"] and not arms["on"]["error"]:
            out[item].append((arms["off"], arms["on"]))
    return out


def cluster_mean_ci(per_item: dict[str, list[float]]) -> tuple[float, float, float]:
    """Mean of the paired deltas and the one-sample t interval over ITEMS (PROTOCOL §11).

    The replicas of one item are correlated, so each item contributes ONE number (the mean of its
    replicas' deltas) and the interval is the closed-form t over those. The first version of this
    file used a percentile bootstrap, which §11 forbids under N = 100 (it covers ~88%).
    """
    from chimera.eval.proportions import mean_t_interval

    items = [statistics.fmean(v) for v in per_item.values() if v]
    _, lo, hi = mean_t_interval(items)
    return statistics.fmean([x for v in per_item.values() for x in v]), lo, hi


def main(path: str) -> int:
    rows = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    spent = sum(r["usd"] for r in rows)
    print(f"runs {len(rows)}  spend US$ {spent:.4f}  errors {sum(1 for r in rows if r['error'])}")
    verdict: dict[str, bool] = {}

    read = _pairs(rows, "read")
    n_read = sum(len(v) for v in read.values())
    print(f"\nREAD  pairs {n_read} (need >= 24)")
    for key, label in (("steps", "P1 steps"), ("completion_tokens", "P2 output tokens")):
        deltas = {i: [on[key] - off[key] for off, on in v] for i, v in read.items()}
        mean, lo, hi = cluster_mean_ci(deltas)
        off_mean = statistics.fmean([off[key] for v in read.values() for off, _ in v])
        rel = mean / off_mean if off_mean else 0.0
        ok = mean < 0 and hi < 0 and rel <= -0.10
        verdict[label] = ok
        print(f"  {label:18} OFF mean {off_mean:8.2f}  delta {mean:+8.2f} ({rel:+.1%})  CI95 [{lo:+.2f}, {hi:+.2f}]  -> {'MEETS' if ok else 'does not meet'}")
    c_off = statistics.fmean([off["ok"] for v in read.values() for off, _ in v])
    c_on = statistics.fmean([on["ok"] for v in read.values() for _, on in v])
    verdict["G1"] = c_on >= c_off - 0.05
    print(f"  G1 correct   OFF {c_off:.1%}  ON {c_on:.1%}  diff {100*(c_on-c_off):+.1f} pp  -> {'holds' if verdict['G1'] else 'FAILS'}")

    edit = _pairs(rows, "edit")
    n_edit = sum(len(v) for v in edit.values())
    e_off = statistics.fmean([off["ok"] for v in edit.values() for off, _ in v]) if n_edit else 0.0
    e_on = statistics.fmean([on["ok"] for v in edit.values() for _, on in v]) if n_edit else 0.0
    per_item = {i: [float(on["ok"]) - float(off["ok"]) for off, on in v] for i, v in edit.items()}
    mean, lo, hi = cluster_mean_ci(per_item) if n_edit else (0.0, 0.0, 0.0)
    verdict["G2"] = e_on >= e_off - 0.05
    from chimera.eval.proportions import tost_mean

    tost = tost_mean([statistics.fmean(v) for v in per_item.values() if v], 0.05) if n_edit else None
    disc_on = sum(1 for v in edit.values() for off, on in v if on["ok"] and not off["ok"])
    disc_off = sum(1 for v in edit.values() for off, on in v if off["ok"] and not on["ok"])
    print(f"\nEDIT  pairs {n_edit} (need >= 22)")
    print(f"  G2 pass      OFF {e_off:.1%}  ON {e_on:.1%}  diff {100*(e_on-e_off):+.1f} pp  CI95(t, over items) [{100*lo:+.1f}, {100*hi:+.1f}] pp  "
          f"discordant ON-only {disc_on} / OFF-only {disc_off}  -> {'holds' if verdict['G2'] else 'FAILS'}")
    if tost is not None:
        print(f"  TOST non-inferiority at -5 pp (reported, not decisive; §12): 90% interval "
              f"[{100*tost.low:+.1f}, {100*tost.high:+.1f}] pp -> non_inferior={tost.non_inferior}")
    for key in ("steps", "completion_tokens"):
        print(f"  secondary {key}: OFF {statistics.fmean([off[key] for v in edit.values() for off, _ in v]):.1f}  "
              f"ON {statistics.fmean([on[key] for v in edit.values() for _, on in v]):.1f}")
    print(f"  ON runs that changed no file: {sum(1 for v in edit.values() for _, on in v if on['files_changed'] == 0)} "
          f"(OFF: {sum(1 for v in edit.values() for off, _ in v if off['files_changed'] == 0)})")

    control = _pairs(rows, "control")
    print("\nCONTROL")
    g3 = True
    for item, v in sorted(control.items()):
        off_n, on_n = sum(1 for off, _ in v if off["ok"]), sum(1 for _, on in v if on["ok"])
        g3 &= on_n >= off_n
        print(f"  {item:14} OFF {off_n}/{len(v)}  ON {on_n}/{len(v)}")
    verdict["G3"] = g3
    print(f"  G3 -> {'holds' if g3 else 'FAILS'}")

    enough = n_read >= 24 and n_edit >= 22
    adopt = enough and all(verdict.values())
    print(f"\nDECISION: {'ADOPT' if adopt else ('INCONCLUSIVE (too few pairs)' if not enough else 'NOT ADOPTED')}  {verdict}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _legacy_defaults import pin_legacy_defaults

    pin_legacy_defaults()  # reads files only; pinned for the entry-point convention
    raise SystemExit(main(sys.argv[1]))
