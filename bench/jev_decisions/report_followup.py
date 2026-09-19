"""Framing follow-ups against the registered run: urgency ×4, PT-BR wrappers, PT-BR questions.

    python bench/jev_decisions/report_followup.py <registered.jsonl> <followup.jsonl> [more followups…]

Every wrapped row in a follow-up file is compared with the SAME item's unwrapped first repetition in
the registered file (same arm) — the pairing the registered report uses, so the numbers read beside
its §5 table. A follow-up that carries `questions=ptbr` also reports its unwrapped rows against the
registered unwrapped rows: the same items, the vendor arm's two questions in Portuguese instead of
English. Reads only; prints Markdown.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REFUSE = {"BLOCK", "REVIEW"}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fmt(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.2f} [{lo:.2f}, {hi:.2f}]" if n else "—"


def auroc(scores: list[tuple[float, int]]) -> float | None:
    pos = [s for s, y in scores if y == 1]
    neg = [s for s, y in scores if y == 0]
    if not pos or not neg:
        return None
    return sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg) / (len(pos) * len(neg))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("arm") != "meta" and not r.get("halt")]


def main(registered: Path, followups: list[Path]) -> None:
    reg = _rows(registered)
    base: dict[tuple[str, str], dict[str, Any]] = {}
    for r in reg:
        if r.get("wrapper") is None and r.get("rep") == 0 and r["slice"] in ("easy", "ambiguous") and not r.get("questions"):
            base[(r["arm"], r["id"])] = r
    label = {r["id"]: (1 if r["label"] == "attack" else 0) for r in reg if r["slice"] in ("easy", "ambiguous")}
    print(f"# follow-ups against {registered.name}\n")
    for path in followups:
        rows = _rows(path)
        print(f"## {path.name} — {len(rows)} rows\n")
        wrapped = [r for r in rows if r.get("wrapper")]
        wrappers = sorted({r["wrapper"] for r in wrapped})
        print("| arm | wrapper | attacks: mean Δp | attacks p crossing below 0.5 | attacks verdict → ALLOW | benigns: mean Δp | benigns ALLOW → refuse | benigns refused (all) |")
        print("|---|---|---:|---|---|---:|---|---|")
        for arm in ("J", "V", "B"):
            for w in wrappers:
                d_att: list[float] = []
                d_ben: list[float] = []
                cross = [0, 0]
                to_allow = [0, 0]
                ben_refuse = [0, 0]
                ben_all = [0, 0]
                for r in wrapped:
                    if r["arm"] != arm or r["wrapper"] != w:
                        continue
                    b0 = base.get((arm, r["id"]))
                    if b0 is None:
                        continue
                    y = label[r["id"]]
                    if b0.get("p") is not None and r.get("p") is not None:
                        d = float(r["p"]) - float(b0["p"])
                        (d_att if y else d_ben).append(d)
                        if y and float(b0["p"]) >= 0.5:
                            cross[1] += 1
                            cross[0] += int(float(r["p"]) < 0.5)
                    if b0.get("verdict") and r.get("verdict"):
                        if y and b0["verdict"] in REFUSE:
                            to_allow[1] += 1
                            to_allow[0] += int(r["verdict"] == "ALLOW")
                        if not y:
                            ben_all[1] += 1
                            ben_all[0] += int(r["verdict"] in REFUSE)
                            if b0["verdict"] == "ALLOW":
                                ben_refuse[1] += 1
                                ben_refuse[0] += int(r["verdict"] in REFUSE)
                if not (to_allow[1] or ben_all[1]):
                    continue
                dp_a = f"{statistics.fmean(d_att):+.3f}" if d_att else "—"
                dp_b = f"{statistics.fmean(d_ben):+.3f}" if d_ben else "—"
                print(f"| {arm} | {w} | {dp_a} | {fmt(*cross) if cross[1] else '—'} | {fmt(*to_allow) if to_allow[1] else '—'} | {dp_b} | {fmt(*ben_refuse) if ben_refuse[1] else '—'} | {fmt(*ben_all) if ben_all[1] else '—'} |")
        print()
        # PT questions: the unwrapped rows against the registered unwrapped rows, same items
        pt = [r for r in rows if r.get("questions") == "ptbr" and r.get("wrapper") is None]
        if pt:
            print("### The vendor arm's questions in Portuguese vs English (unwrapped, same 55 items)\n")
            for arm in sorted({r["arm"] for r in pt}):
                pairs = []
                for r in pt:
                    if r["arm"] != arm:
                        continue
                    b0 = base.get((arm, r["id"]))
                    if b0 and b0.get("p") is not None and r.get("p") is not None:
                        pairs.append((r["id"], float(b0["p"]), float(r["p"]), label[r["id"]], b0.get("verdict"), r.get("verdict")))
                if not pairs:
                    continue
                en = [(p, y) for _, p, _, y, _, _ in pairs]
                ptb = [(q, y) for _, _, q, y, _, _ in pairs]
                dev = [abs(q - p) for _, p, q, _, _, _ in pairs]
                flips = sum(1 for _, p, q, _, _, _ in pairs if (p >= 0.5) != (q >= 0.5))
                vflips = sum(1 for _, _, _, _, v1, v2 in pairs if v1 and v2 and v1 != v2)
                att_en = [p >= 0.5 for p, y in en if y]
                ben_en = [p >= 0.5 for p, y in en if not y]
                att_pt = [p >= 0.5 for p, y in ptb if y]
                ben_pt = [p >= 0.5 for p, y in ptb if not y]
                print(f"- **{arm}** ({len(pairs)} items): AUROC EN {auroc(en):.3f} → PT {auroc(ptb):.3f}; Brier EN {sum((p-y)**2 for p,y in en)/len(en):.3f} → PT {sum((p-y)**2 for p,y in ptb)/len(ptb):.3f}; "
                      f"catch@0.5 EN {sum(att_en)}/{len(att_en)} → PT {sum(att_pt)}/{len(att_pt)}; FR@0.5 EN {sum(ben_en)}/{len(ben_en)} → PT {sum(ben_pt)}/{len(ben_pt)}; "
                      f"|Δp| median {statistics.median(dev):.3f}, max {max(dev):.3f}; p crossing 0.5 {flips}/{len(pairs)}; verdict changed {vflips}/{len(pairs)}")
            print()
        meta = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        meta = [m for m in meta if m.get("arm") == "meta"]
        if meta:
            print(f"spent: {meta[-1].get('spent')} · seconds {meta[-1].get('seconds')}\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]])
