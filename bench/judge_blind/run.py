"""Does the fusion judge's verdict follow the model name and the position it is shown?
Registered in `PREREGISTRATION.md` before any model call.

Two phases. `--collect` asks three weak models GSM8K questions until 40 have at least one right and
one wrong answer. `--run` pushes each such trio through the production judge -> synthesiser path
(`FusionEngine._aggregate`) nine times: six with the production panel's names rotated over the same
texts in two orders, three blind (`FusionConfig.blind_panel=True`). The final answer is graded exactly.

    python bench/judge_blind/run.py --collect
    python bench/judge_blind/run.py --run
    python bench/judge_blind/run.py --report bench/judge_blind/results/<tag>.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bench" / "llm_benchmarks"))

from datasets import (  # noqa: E402  (bench/llm_benchmarks, not the HF package)
    gsm8k_reference,
    load_gsm8k,
)
from gsm8k import extract_answer, normalise  # noqa: E402

from chimera.config import (  # noqa: E402
    _DEFAULT_JUDGE,
    _DEFAULT_PANEL,
    _DEFAULT_SYNTHESIZER,
    get_settings,
)
from chimera.eval.anytime import wilson_bounds  # noqa: E402
from chimera.eval.paired import compare_paired  # noqa: E402
from chimera.fusion.engine import FusionConfig, FusionEngine, PanelResponse  # noqa: E402
from chimera.orchestration.receipts import price_completion  # noqa: E402

WEAK_PANEL = (
    "openrouter/mistralai/mistral-small-3.2-24b-instruct",
    "openrouter/meta-llama/llama-3.3-70b-instruct",
    "openrouter/openai/gpt-oss-20b",
)
SHOWN_SLUGS = tuple(_DEFAULT_PANEL)  # the names the judge is shown, rotated over the same texts
SUFFIX = "\n\nEnd your answer with a line of the form `ANSWER: <number>`."
ORDERS = {"orig": (0, 1, 2), "rev": (2, 1, 0)}
_VENDOR = re.compile(r"opus|gpt|gemini|anthropic|openai|google|claude", re.I)


# --- phase 1: collect disagreement items ----------------------------------------------------------------

@dataclass
class Item:
    item_id: str
    question: str
    reference: str
    answers: list[str]
    """Three answer texts, in WEAK_PANEL order."""
    writers: list[str]
    correct: list[bool]
    usd: float | None


def _ask(gateway: Any, model: str, question: str) -> tuple[str, float | None]:
    result = gateway.complete(
        [{"role": "user", "content": question + SUFFIX}], model=model, temperature=0.3, max_tokens=1200,
    )
    cost = price_completion(result)
    return (result.content or "").strip(), (None if cost.unpriced else cost.usd)


def collect(out: Path, *, target: int, cap: int, workers: int) -> int:
    from concurrent.futures import ThreadPoolExecutor

    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    problems = load_gsm8k()
    rng = random.Random(7)
    order = list(range(len(problems)))
    rng.shuffle(order)
    have = 0
    if out.exists():
        have = sum(1 for line in out.read_text(encoding="utf-8").splitlines() if line.strip())
    asked, spent = 0, 0.0
    with out.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=workers) as pool:
        for idx in order:
            if have >= target or asked >= cap:
                break
            asked += 1
            problem = problems[idx]
            question = problem["question"]
            reference = normalise(gsm8k_reference(problem["answer"]))
            results = list(pool.map(_ask, [gateway] * 3, WEAK_PANEL, [question] * 3))
            answers = [r[0] for r in results]
            usd = sum(r[1] or 0.0 for r in results)
            spent += usd
            correct = [extract_answer(a) == reference for a in answers]
            if all(answers) and any(correct) and not all(correct):
                have += 1
                item = Item(item_id=f"gsm8k-{idx}", question=question, reference=reference, answers=answers,
                            writers=list(WEAK_PANEL), correct=correct, usd=usd)
                fh.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
                fh.flush()
            print(f"  asked {asked:>3} · kept {have:>2} · this one {''.join('R' if c else 'W' for c in correct)}"
                  f"  Σ US$ {spent:.4f}", flush=True)
    print(f"{have} items in {out} after {asked} questions")
    return 0


def load_items(path: Path) -> list[Item]:
    return [Item(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- phase 2: the judge -> synthesiser runs --------------------------------------------------------------

@dataclass
class Run:
    item_id: str
    arm: str
    rotation: int
    order: str
    positions: list[int]
    """Answer index (into item.answers) at each position the judge saw, 0-based."""
    slug_on: list[str]
    """The name shown on each answer index (named arm) or 'A'/'B'/'C' letters (blind arm)."""
    correct_positions: list[int]
    """1-based positions of the correct answer(s) as shown."""
    slugs_on_correct: list[str]
    final: str
    extracted: str
    passed: bool
    judge_mentions_vendor: bool
    judge_analysis: str
    usd: float | None
    prompt_tokens: int
    completion_tokens: int
    seconds: float


def _engine(blind: bool) -> FusionEngine:
    from chimera.providers import LLMGateway

    config = FusionConfig(
        panel=list(SHOWN_SLUGS), judge=_DEFAULT_JUDGE, synthesizer=_DEFAULT_SYNTHESIZER, blind_panel=blind,
    )
    return FusionEngine(LLMGateway(), config)


def one(item: Item, arm: str, rotation: int, order: str, shuffle_seed: int) -> Run:
    if order == "shuffle":
        positions = [0, 1, 2]
        random.Random(shuffle_seed).shuffle(positions)
    else:
        positions = list(ORDERS[order])
    if arm == "named":
        slug_on = [SHOWN_SLUGS[(k + rotation) % 3] for k in range(3)]
    else:
        slug_on = ["?", "?", "?"]  # the engine assigns letters itself and reports the permutation
    panel = [PanelResponse(model=slug_on[k] if arm == "named" else item.writers[k], content=item.answers[k])
             for k in positions]
    engine = _engine(arm == "blind")
    messages = [{"role": "user", "content": item.question + SUFFIX}]
    t0 = time.monotonic()
    analysis, final, aggregation, judge, synth, shown = engine._aggregate(messages, panel)
    if aggregation != "synth" or judge is None or synth is None:
        raise RuntimeError(f"judge was bypassed ({aggregation})")
    if arm == "blind":
        # `shown[p]` indexes `panel`, which was already permuted by `positions`; compose the two.
        positions = [positions[i] for i in (shown or range(3))]
        slug_on = ["?"] * 3
        for p, k in enumerate(positions):
            slug_on[k] = chr(ord("A") + p)
    correct_positions = [p + 1 for p, k in enumerate(positions) if item.correct[k]]
    slugs_on_correct = [slug_on[k] for k in range(3) if item.correct[k]]
    extracted = extract_answer(final)
    usd, ptok, ctok, unpriced = 0.0, 0, 0, False
    for r in (judge, synth):
        cost = price_completion(r)
        usd += cost.usd
        unpriced = unpriced or cost.unpriced is not None
        ptok += r.prompt_tokens or 0
        ctok += r.completion_tokens or 0
    return Run(
        item_id=item.item_id, arm=arm, rotation=rotation, order=order, positions=positions, slug_on=slug_on,
        correct_positions=correct_positions, slugs_on_correct=slugs_on_correct, final=final,
        extracted=extracted, passed=(extracted == item.reference), judge_mentions_vendor=bool(_VENDOR.search(analysis)),
        judge_analysis=analysis, usd=(None if unpriced else usd), prompt_tokens=ptok, completion_tokens=ctok,
        seconds=round(time.monotonic() - t0, 1),
    )


def plan_for(item: Item, index: int) -> list[tuple[str, int, str, int]]:
    named = [("named", r, o, 0) for r in range(3) for o in ("orig", "rev")]
    blind = [("blind", 0, "orig", 0), ("blind", 0, "rev", 0), ("blind", 0, "shuffle", 1000 + index)]
    return named + blind


# --- reporting ------------------------------------------------------------------------------------------

def report(path: Path, items_path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    items = {i.item_id: i for i in load_items(items_path)}
    lines = [f"# judge_blind — {len(rows)} pipeline runs over {len({r['item_id'] for r in rows})} items, "
             f"US$ {sum(r['usd'] or 0 for r in rows):.4f}", ""]
    comp = defaultdict(int)
    for it in items.values():
        comp[f"{sum(it.correct)} right / {3 - sum(it.correct)} wrong"] += 1
    lines.append(f"corpus composition: {dict(comp)}")
    lines.append("")
    lines.append("| arm | runs | passed | Wilson 95% | judge names a vendor | mean tokens/run | US$ |")
    lines.append("|---|---:|---:|---|---:|---:|---:|")
    per_item: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for arm in ("named", "blind"):
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        for r in rs:
            per_item[arm][r["item_id"]].append(r["passed"])
        k = sum(r["passed"] for r in rs)
        lo, hi = wilson_bounds(k, len(rs))
        mentions = sum(r["judge_mentions_vendor"] for r in rs)
        toks = sum(r["prompt_tokens"] + r["completion_tokens"] for r in rs) / len(rs)
        lines.append(f"| `{arm}` | {len(rs)} | **{k}/{len(rs)}** ({k / len(rs):.2f}) | [{lo:.2f}, {hi:.2f}] | "
                     f"{mentions}/{len(rs)} | {toks:,.0f} | {sum(r['usd'] or 0 for r in rs):.4f} |")
    lines.append("")
    common = sorted(set(per_item["named"]) & set(per_item["blind"]))
    if common:
        maj = lambda v: sum(v) * 2 > len(v)  # noqa: E731
        pr = compare_paired([maj(per_item["named"][i]) for i in common], [maj(per_item["blind"][i]) for i in common],
                            baseline_name="named", treatment_name="blind")
        lo, hi = pr.diff_ci
        mean_named = sum(sum(v) / len(v) for v in per_item["named"].values()) / len(per_item["named"])
        mean_blind = sum(sum(v) / len(v) for v in per_item["blind"].values()) / len(per_item["blind"])
        lines.append(f"- **primary** per-item majority, named → blind: {pr.baseline_rate:.2f} → {pr.treatment_rate:.2f} "
                     f"(Δ {pr.delta:+.2f}, Newcombe 95% [{lo:+.2f}, {hi:+.2f}]; discordant {pr.discordant}: "
                     f"blind-only {pr.treatment_only}, named-only {pr.baseline_only}; "
                     f"{'significant' if pr.significant else 'not significant'}); per-item mean accuracy "
                     f"named {mean_named:.3f}, blind {mean_blind:.3f}")
    # vendor effect: named runs, grouped by the slug shown on a correct answer
    lines.append("")
    lines.append("### Vendor effect (named runs): accuracy when a correct answer carried this name")
    lines.append("")
    lines.append("| name on a correct answer | runs | passed | Wilson 95% |")
    lines.append("|---|---:|---:|---|")
    for slug in SHOWN_SLUGS:
        rs = [r for r in rows if r["arm"] == "named" and slug in r["slugs_on_correct"]]
        if rs:
            k = sum(r["passed"] for r in rs)
            lo, hi = wilson_bounds(k, len(rs))
            lines.append(f"| `{slug.split('/')[-1]}` | {len(rs)} | {k} ({k / len(rs):.2f}) | [{lo:.2f}, {hi:.2f}] |")
    for arm in ("named", "blind"):
        lines.append("")
        lines.append(f"### Position effect ({arm} runs): accuracy when a correct answer sat at this position")
        lines.append("")
        lines.append("| position of a correct answer | runs | passed | Wilson 95% |")
        lines.append("|---|---:|---:|---|")
        for pos in (1, 2, 3):
            rs = [r for r in rows if r["arm"] == arm and pos in r["correct_positions"]]
            if rs:
                k = sum(r["passed"] for r in rs)
                lo, hi = wilson_bounds(k, len(rs))
                lines.append(f"| {pos} | {len(rs)} | {k} ({k / len(rs):.2f}) | [{lo:.2f}, {hi:.2f}] |")
    lines.append("")
    lines.append("### Ten judge analyses that name a vendor (named runs) — read these")
    lines.append("")
    rng = random.Random(7)
    naming = [r for r in rows if r["arm"] == "named" and r["judge_mentions_vendor"]]
    for r in rng.sample(naming, min(10, len(naming))):
        snippet = " ".join(r["judge_analysis"].split())
        m = _VENDOR.search(snippet)
        start = max(0, (m.start() if m else 0) - 150)
        lines.append(f"- `{r['item_id']}` r{r['rotation']} {r['order']} · passed={r['passed']} · …{snippet[start:start + 380]}…")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--items", default=str(Path(__file__).with_name("results") / "items.jsonl"))
    ap.add_argument("--out", default="")
    ap.add_argument("--target", type=int, default=40)
    ap.add_argument("--cap", type=int, default=400)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    items_path = Path(args.items)
    if args.report:
        print(report(Path(args.report), items_path))
        return 0
    settings = get_settings()
    if not settings.has_any_key():
        print("no provider key in the environment", file=sys.stderr)
        return 2
    if settings.cache:
        print("CHIMERA_CACHE is on — runs would be served from cache; aborting", file=sys.stderr)
        return 2
    items_path.parent.mkdir(parents=True, exist_ok=True)
    if args.collect:
        return collect(items_path, target=args.target, cap=args.cap, workers=3)
    if not args.run:
        ap.print_help()
        return 1
    items = load_items(items_path)
    if args.limit:
        items = items[: args.limit]
    out = Path(args.out) if args.out else Path(__file__).with_name("results") / f"{time.strftime('%Y-%m-%d')}-runs.jsonl"
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["item_id"], r["arm"], r["rotation"], r["order"]))
    plan = [(item, arm, rot, order, seed) for i, item in enumerate(items) for arm, rot, order, seed in plan_for(item, i)
            if (item.item_id, arm, rot, order) not in done]
    print(f"{len(plan)} runs to make, {len(done)} on disk; judge {_DEFAULT_JUDGE}, synth {_DEFAULT_SYNTHESIZER}")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    spent = 0.0
    with out.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, item, arm, rot, order, seed): (item.item_id, arm, rot, order)
                   for item, arm, rot, order, seed in plan}
        for fut in as_completed(futures):
            iid, arm, rot, order = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001 — a provider hiccup must not lose the file
                print(f"  {iid:<12} {arm:<6} r{rot} {order:<7} ERROR {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                continue
            spent += r.usd or 0.0
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {iid:<12} {arm:<6} r{rot} {order:<7} {'PASS' if r.passed else 'FAIL'} "
                  f"correct@{r.correct_positions} {r.seconds}s  Σ US$ {spent:.4f}", flush=True)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
