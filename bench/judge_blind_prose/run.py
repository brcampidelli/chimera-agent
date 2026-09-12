"""Named vs blind on the prose corpus. Registered in `PREREGISTRATION.md` before any model call.

Same design as `bench/judge_blind` — imported from there by path — but the panel candidates are the
authored answers of `corpus.py`, two faithful and one that contradicts a stated fact, and the grade
is a token check on the synthesised final (`kept` / `propagated` / `omitted`), not a match against a
reference. The question: when the contradicting candidate wears a stronger vendor's name, does the
judge propagate its wrong token into the synthesis more often than when the names are hidden?

    python bench/judge_blind_prose/run.py --run   [--out results/<tag>.jsonl]
    python bench/judge_blind_prose/run.py --report results/<tag>.jsonl
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from bench.judge_blind_prose.corpus import Item, corpus, grade  # noqa: E402
from chimera.fusion.engine import PanelResponse  # noqa: E402
from chimera.eval.anytime import wilson_bounds  # noqa: E402
from chimera.eval.paired import compare_paired  # noqa: E402
from chimera.orchestration.receipts import price_completion  # noqa: E402


def _load_judge_blind() -> Any:
    path = REPO / "bench" / "judge_blind" / "run.py"
    spec = importlib.util.spec_from_file_location("judge_blind_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["judge_blind_run"] = module
    spec.loader.exec_module(module)
    return module


jb = _load_judge_blind()
FLAWED = 2  # the index of the flawed candidate in item.answers()


@dataclass
class Run:
    item_id: str
    arm: str
    rotation: int
    order: str
    flawed_position: int  # 1-based position the flawed candidate was shown at
    flawed_label: str  # vendor slug on the flawed candidate (named) or letter A/B/C (blind)
    verdict: str  # kept | propagated | omitted
    judge_mentions_vendor: bool
    final: str
    usd: float | None
    seconds: float


def one(item: Item, arm: str, rotation: int, order: str, shuffle_seed: int) -> Run:
    answers = item.answers()
    if order == "shuffle":
        positions = [0, 1, 2]
        random.Random(shuffle_seed).shuffle(positions)
    else:
        positions = list(jb.ORDERS[order])
    slug_on = [jb.SHOWN_SLUGS[(k + rotation) % 3] for k in range(3)] if arm == "named" else ["?", "?", "?"]
    panel = [
        PanelResponse(model=slug_on[k] if arm == "named" else f"writer{k}", content=answers[k])
        for k in positions
    ]
    engine = jb._engine(arm == "blind")
    messages = [{"role": "user", "content": item.passage + "\n\n" + item.instruction}]
    t0 = time.monotonic()
    analysis, final, aggregation, judge, synth, shown = jb._retrying(lambda: engine._aggregate(messages, panel))
    if aggregation != "synth" or judge is None or synth is None:
        raise RuntimeError(f"judge was bypassed ({aggregation})")
    # Compose `positions` (my permutation) with `shown` (the engine's blind re-shuffle) to get the
    # answer index at each position the judge actually saw — exactly as judge_blind does.
    shown_order = [positions[i] for i in (shown or range(3))]
    flawed_position = shown_order.index(FLAWED) + 1
    if arm == "named":
        flawed_label = slug_on[FLAWED].split("/")[-1]
    else:
        flawed_label = chr(ord("A") + flawed_position - 1)
    usd, unpriced = 0.0, False
    for r in (judge, synth):
        cost = price_completion(r)
        usd += cost.usd
        unpriced = unpriced or cost.unpriced is not None
    return Run(
        item_id=item.id, arm=arm, rotation=rotation, order=order,
        flawed_position=flawed_position, flawed_label=flawed_label,
        verdict=grade(final, item), judge_mentions_vendor=bool(jb._VENDOR.search(analysis)),
        final=final, usd=(None if unpriced else usd), seconds=round(time.monotonic() - t0, 1),
    )


def plan_for(index: int) -> list[tuple[str, int, str, int]]:
    named = [("named", r, o, 0) for r, o in jb.NAMED_DESIGN]
    blind = [("blind", 0, "o0", 0), ("blind", 0, "o1", 0), ("blind", 0, "shuffle", 2000 + index)]
    return named + blind


def run(out: Path) -> None:
    items = corpus()
    out.parent.mkdir(parents=True, exist_ok=True)
    done: set[tuple[str, str, int, str]] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["item_id"], r["arm"], r["rotation"], r["order"]))
    spent = 0.0
    with out.open("a", encoding="utf-8") as fh:
        for index, item in enumerate(items):
            for arm, rotation, order, seed in plan_for(index):
                if (item.id, arm, rotation, order) in done:
                    continue
                try:
                    r = one(item, arm, rotation, order, seed)
                except Exception as exc:  # noqa: BLE001 — a run the providers could not finish is a halt, skipped
                    print(f"  {item.id:<14} {arm:<6} r{rotation} {order:<7} SKIPPED {type(exc).__name__}: {str(exc)[:80]}", flush=True)
                    continue
                spent += r.usd or 0.0
                fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
                fh.flush()
                print(f"  {item.id:<14} {arm:<6} r{rotation} {order:<7} flawed@{r.flawed_position} "
                      f"[{r.flawed_label:<16}] -> {r.verdict:<10} Σ US$ {spent:.4f}", flush=True)
    print(f"done, US$ {spent:.4f}")
    report(out)


def _rate(rows: list[dict[str, Any]], verdict: str) -> tuple[int, int]:
    scored = [r for r in rows if r["verdict"] != "omitted"]
    return sum(r["verdict"] == verdict for r in scored), len(scored)


def report(path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    lines = [f"# judge_blind_prose — {len(rows)} runs over {len({r['item_id'] for r in rows})} items, "
             f"US$ {sum(r['usd'] or 0 for r in rows):.4f}", ""]
    lines.append("Grade is on the synthesised final: `propagated` = the flawed candidate's wrong token "
                 "reached the output, `kept` = the source's right token did and the wrong one did not, "
                 "`omitted` = neither (the fact was dropped; not scored).")
    lines.append("")
    lines.append("| arm | runs | propagated | kept | omitted | propagation rate (of scored) | Wilson 95% | judge names a vendor |")
    lines.append("|---|---:|---:|---:|---:|---:|---|---:|")
    per_item: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for arm in ("named", "blind"):
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        prop, scored = _rate(rs, "propagated")
        kept = sum(r["verdict"] == "kept" for r in rs)
        omit = sum(r["verdict"] == "omitted" for r in rs)
        lo, hi = wilson_bounds(prop, scored) if scored else (float("nan"), float("nan"))
        mentions = sum(r["judge_mentions_vendor"] for r in rs)
        rate = prop / scored if scored else float("nan")
        for r in rs:
            if r["verdict"] != "omitted":
                per_item[arm][r["item_id"]].append(int(r["verdict"] == "propagated"))
        lines.append(f"| `{arm}` | {len(rs)} | {prop} | {kept} | {omit} | **{rate:.2f}** | "
                     f"[{lo:.2f}, {hi:.2f}] | {mentions}/{len(rs)} |")
    lines.append("")

    # The bias question: propagation grouped by the vendor shown on the flawed candidate (named arm).
    lines.append("### Named arm — propagation when the flawed candidate wore this vendor's name")
    lines.append("")
    lines.append("| vendor on the flawed answer | scored | propagated | rate | Wilson 95% |")
    lines.append("|---|---:|---:|---:|---|")
    named = [r for r in rows if r["arm"] == "named" and r["verdict"] != "omitted"]
    for slug in sorted({r["flawed_label"] for r in named}):
        rs = [r for r in named if r["flawed_label"] == slug]
        prop = sum(r["verdict"] == "propagated" for r in rs)
        lo, hi = wilson_bounds(prop, len(rs))
        lines.append(f"| `{slug}` | {len(rs)} | {prop} | {prop / len(rs):.2f} | [{lo:.2f}, {hi:.2f}] |")
    lines.append("")

    # Position control: propagation grouped by where the flawed candidate was shown.
    lines.append("### Propagation by the flawed candidate's position (both arms)")
    lines.append("")
    lines.append("| position | scored | propagated | rate |")
    lines.append("|---|---:|---:|---:|")
    scored_rows = [r for r in rows if r["verdict"] != "omitted"]
    for pos in (1, 2, 3):
        rs = [r for r in scored_rows if r["flawed_position"] == pos]
        if rs:
            prop = sum(r["verdict"] == "propagated" for r in rs)
            lines.append(f"| {pos} | {len(rs)} | {prop} | {prop / len(rs):.2f} |")
    lines.append("")

    # Per item, because ten items are small enough to read.
    lines.append("### Per item — propagation rate (scored runs), named vs blind")
    lines.append("")
    lines.append("| item | named | blind |")
    lines.append("|---|---:|---:|")
    for item_id in sorted({r["item_id"] for r in rows}):
        n = per_item["named"].get(item_id, [])
        b = per_item["blind"].get(item_id, [])
        ns = f"{sum(n)}/{len(n)}" if n else "—"
        bs = f"{sum(b)}/{len(b)}" if b else "—"
        lines.append(f"| `{item_id}` | {ns} | {bs} |")
    lines.append("")

    # Primary: per-item propagation majority, named vs blind, paired (Newcombe), mirroring judge_blind.
    common = sorted(set(per_item["named"]) & set(per_item["blind"]))
    if common:
        maj = lambda v: sum(v) * 2 > len(v)  # noqa: E731 — a majority of the item's scored runs propagated
        pr = compare_paired(
            [maj(per_item["named"][i]) for i in common], [maj(per_item["blind"][i]) for i in common],
            baseline_name="named", treatment_name="blind",
        )
        lo, hi = pr.diff_ci
        lines.append(f"- **primary** per-item propagation majority, named → blind: {pr.baseline_rate:.2f} → "
                     f"{pr.treatment_rate:.2f} (Δ {pr.delta:+.2f}, Newcombe 95% [{lo:+.2f}, {hi:+.2f}]; "
                     f"discordant {pr.discordant}: blind-only {pr.treatment_only}, named-only {pr.baseline_only}; "
                     f"{'significant' if pr.significant else 'not significant'})")

    text = "\n".join(lines) + "\n"
    print(text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--out", type=Path, default=REPO / "bench/judge_blind_prose/results/run.jsonl")
    args = ap.parse_args()
    if args.report:
        report(args.report)
    elif args.run:
        run(args.out)
    else:
        ap.error("pass --run or --report")


if __name__ == "__main__":
    main()
