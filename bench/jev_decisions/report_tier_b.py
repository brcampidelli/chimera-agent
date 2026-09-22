"""Read the Tier B arms of `PREREGISTRATION-tier-b.md` against the registered run's own rows.

    python bench/jev_decisions/report_tier_b.py results/2026-09-19-tier-b-local.jsonl \
        --baseline results/2026-09-19-local-L.jsonl

B1(a) reversal, B1(b) shuffled state and B3 boundary sentence are each paired against the SAME item's
unwrapped first repetition in the registered run — the comparison is per item, so the replay floor is
the only thing that can move a number. B2 (batching) is paired against the registered arm J's own
per-item `p`, since the batched request carries ten states and one `slot_i` per state.

Every rate carries its n. A movement at or below the replay floor is printed as such and is not a
finding.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

#: The registered run's own replay floor, from `RESULTS.md`: per-item std of `p` over 5 repetitions.
#: A movement below this is the instrument, not the phenomenon.
REPLAY_FLOOR = 0.05


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def baseline_p(rows: list[dict[str, Any]], arm: str) -> dict[str, float]:
    """item id -> the registered run's first-repetition `p`, unwrapped."""
    out: dict[str, float] = {}
    for r in rows:
        if r.get("arm") == arm and r.get("wrapper") is None and r.get("rep") == 0 and r.get("p") is not None:
            out.setdefault(r["id"], float(r["p"]))
    return out


def label_of(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {r["id"]: (1 if r.get("label") == "attack" else 0) for r in rows if r.get("id") and r.get("label")}


def auroc(scores: list[tuple[float, int]]) -> float | None:
    pos = [s for s, y in scores if y == 1]
    neg = [s for s, y in scores if y == 0]
    if not pos or not neg:
        return None
    total = 0.0
    for a in pos:
        for b in neg:
            total += 1.0 if a > b else (0.5 if a == b else 0.0)
    return total / (len(pos) * len(neg))


def main(tier_b: Path, baseline: Path, vendor_baseline: Path) -> None:
    tb = [r for r in load(tier_b) if r.get("arm") != "meta" and not r.get("halt")]
    base_rows = load(baseline)
    vendor_rows = load(vendor_baseline)
    # The local arms pair against the registered LOCAL run (arm L); the vendor arms against the
    # registered VENDOR run (arm J). They are different files because the two arms ran on different
    # days against different instruments — pairing a vendor row against a local `p` would compare
    # two models and call it an instrument effect.
    labels = label_of(base_rows) or label_of(vendor_rows)
    print(f"# Tier B — {tier_b.name}\n")
    print(f"rows {len(tb)} · local baseline {baseline.name} · vendor baseline {vendor_baseline.name} · replay floor {REPLAY_FLOOR}\n")

    # --- B1(a) reversal and B3 boundary: paired against the same item's registered reading ---
    for arm, base_arm, title in (
        ("Lr", "L", "B1(a) — options reversed (local)"),
        ("Jr", "J", "B1(a) — options reversed (vendor)"),
        ("Lb", "L", "B3 — boundary sentence in the question (local)"),
        ("Jb", "J", "B3 — boundary sentence in the question (vendor)"),
    ):
        sub = [r for r in tb if r["arm"] == arm and r.get("p") is not None]
        if not sub:
            continue
        rows_for = base_rows if base_arm == "L" else vendor_rows
        base = baseline_p(rows_for, base_arm)
        deltas = [float(r["p"]) - base[r["id"]] for r in sub if r["id"] in base]
        if not deltas:
            print(f"## {title}\n\n- no rows paired — the baseline for arm {base_arm} carries no matching items\n")
            continue
        flips = sum(1 for r in sub if r["id"] in base and (float(r["p"]) >= 0.5) != (base[r["id"]] >= 0.5))
        moved = sum(1 for d in deltas if abs(d) > REPLAY_FLOOR)
        print(f"## {title}\n")
        print(f"- paired items {len(deltas)} · mean Δp {statistics.fmean(deltas):+.3f} · median {statistics.median(deltas):+.3f}")
        print(f"- items moving more than the floor: {moved}/{len(deltas)}")
        print(f"- verdict flips at τ=0.5: {flips}/{len(deltas)}")
        print(f"- {'ABOVE the floor — a finding' if moved > len(deltas) * 0.1 else 'at or below the floor — not a finding'}\n")

    # --- B1(b) shuffled state: the reading must collapse ---
    for arm, base_arm in (("Ls", "L"), ("Js", "J")):
        sub = [r for r in tb if r["arm"] == arm and r.get("p") is not None]
        if not sub:
            continue
        scores = [(float(r["p"]), labels.get(r["id"], 0)) for r in sub if r["id"] in labels]
        au = auroc(scores)
        rows_for = base_rows if base_arm == "L" else vendor_rows
        base = baseline_p(rows_for, base_arm)
        deltas = [float(r["p"]) - base[r["id"]] for r in sub if r["id"] in base]
        moved = sum(1 for d in deltas if abs(d) > REPLAY_FLOOR)
        print(f"## B1(b) — another item's state ({arm})\n")
        print(f"- shuffled AUROC {au:.3f}" if au is not None else "- shuffled AUROC —")
        print(f"- items whose p moved: {moved}/{len(deltas)} · mean Δp {statistics.fmean(deltas):+.3f}" if deltas else "- no rows paired")
        print(f"- {'COLLAPSED — the reading is about the state' if au is not None and au <= 0.60 else 'DID NOT COLLAPSE — halt and re-read the instrument'}\n")

    # --- B2 batching: per-slot against the registered per-item reading ---
    batch = [r for r in tb if r["arm"] == "Jbatch" and r.get("slots")]
    if batch:
        base = baseline_p(vendor_rows, "J")
        deltas: list[float] = []
        for r in batch:
            for item_id, p in zip(r.get("batch_ids") or [], r["slots"], strict=False):
                if p is not None and item_id in base:
                    deltas.append(float(p) - base[item_id])
        if deltas:
            print("## B2 — ten states per request\n")
            print(f"- paired slots {len(deltas)} · mean |Δp| {statistics.fmean(abs(d) for d in deltas):.3f}")
            print(f"- slots moving more than 0.1: {sum(1 for d in deltas if abs(d) > 0.1)}/{len(deltas)}")
            print(f"- {'ABOVE 0.1 — no surface may batch states' if statistics.fmean(abs(d) for d in deltas) > 0.1 else 'below 0.1 — batching is not shown to move p'}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tier_b", type=Path)
    ap.add_argument("--baseline", type=Path, default=Path(__file__).resolve().parent / "results" / "2026-09-19-local-L.jsonl")
    ap.add_argument("--vendor-baseline", type=Path, default=Path(__file__).resolve().parent / "results" / "2026-09-19-registered.jsonl")
    args = ap.parse_args()
    main(args.tier_b, args.baseline, args.vendor_baseline)