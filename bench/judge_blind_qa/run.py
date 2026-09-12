"""On facts the judge does not know, does its verdict follow the name or the position?
Registered in `PREREGISTRATION.md` before any model call. The instrument `bench/judge_blind_hard`
priced: a corpus the judge cannot re-derive.

Same pipeline, same design, same report as `bench/judge_blind` — imported from there by path — with
SimpleQA questions (OpenAI simple-evals, MIT; fetched at run time) on which the production judge,
asked twice closed-book, was wrong twice, and three non-reasoning writers disagreed.

    python bench/judge_blind_qa/run.py --collect
    python bench/judge_blind_qa/run.py --run
    python bench/judge_blind_qa/run.py --report bench/judge_blind_qa/results/<tag>.jsonl
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import sys
import time
import unicodedata
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bench" / "llm_benchmarks"))

from chimera.config import _DEFAULT_JUDGE, get_settings  # noqa: E402
from chimera.orchestration.receipts import price_completion  # noqa: E402


def _load_judge_blind() -> Any:
    """`bench/judge_blind/run.py` by path — the pipeline, the design, the report."""
    import importlib.util

    path = REPO / "bench" / "judge_blind" / "run.py"
    spec = importlib.util.spec_from_file_location("judge_blind_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["judge_blind_run"] = module
    spec.loader.exec_module(module)
    return module


jb = _load_judge_blind()

DATASET_URL = "https://openaipublic.blob.core.windows.net/simple-evals/simple_qa_test_set.csv"
WRITERS = (
    "openrouter/moonshotai/kimi-k2",
    "openrouter/meta-llama/llama-3.3-70b-instruct",
    "openrouter/google/gemma-3-27b-it",
)
WRITER_MAX_TOKENS = 300
JUDGE_ALONE_ATTEMPTS = 2
SAMPLE_SEED = 7
SAMPLE_CAP = 400
MAX_REFERENCE_TOKENS = 5
MAX_ANSWER_TOKENS = 12
QA_SUFFIX = (
    "\n\nAnswer from memory, without hedging and without listing alternatives. End your answer with a "
    "line of the form `ANSWER: <the answer, as short as possible>`."
)
# The pipeline's writers and judge see the same instruction the collection used.
jb.SUFFIX = QA_SUFFIX

_ANSWER_LINE = re.compile(r"^\s*\**ANSWER\**\s*[:：]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_NUM_SEP = re.compile(r"(?<=\d),(?=\d{3}\b)")
_TOKEN = re.compile(r"[a-z0-9]+")


def answer_line(text: str) -> str:
    """The content of the last `ANSWER:` line, or the last non-empty line."""
    found = _ANSWER_LINE.findall(text or "")
    if found:
        return found[-1].strip()
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def normalise_text(text: str) -> list[str]:
    """Lower-case, strip accents, drop thousands separators and punctuation; the tokens that remain."""
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = _NUM_SEP.sub("", s)
    return _TOKEN.findall(s)


def grade(text: str, reference: str) -> bool:
    """Every token of the reference among the answer line's tokens, and the line short enough to be
    one answer rather than a list. Strict on purpose; the same rule for every text."""
    ref = normalise_text(reference)
    ans = normalise_text(answer_line(text))
    if not ref or not ans or len(ans) > MAX_ANSWER_TOKENS:
        return False
    return set(ref) <= set(ans)


def load_simpleqa() -> list[dict[str, str]]:
    with urllib.request.urlopen(DATASET_URL, timeout=120) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(raw)))
    return [r for r in rows if len(normalise_text(r["answer"])) <= MAX_REFERENCE_TOKENS]


def _ask(gateway: Any, model: str, question: str, *, temperature: float) -> tuple[str, float | None]:
    result = jb._retrying(lambda: gateway.complete(
        [{"role": "user", "content": question + QA_SUFFIX}], model=model, temperature=temperature,
        max_tokens=WRITER_MAX_TOKENS,
    ))
    cost = price_completion(result)
    return (result.content or "").strip(), (None if cost.unpriced else cost.usd)


def collect(out: Path, *, target: int, cap: int, workers: int, sample_index: int = 1) -> int:
    from concurrent.futures import ThreadPoolExecutor

    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    rows = load_simpleqa()
    rng = random.Random(SAMPLE_SEED)
    rng.shuffle(rows)
    # Sample k is the k-th slice of `cap` questions of the one seeded shuffle, so a second sample
    # (addendum 2) shares no question with the first and needs no second seed to be disjoint.
    sample = rows[(sample_index - 1) * cap : sample_index * cap]
    have = {jb.Item(**json.loads(line)).item_id for line in out.read_text(encoding="utf-8").splitlines() if line.strip()} if out.exists() else set()
    log = out.with_name(out.name.replace("items", "collect-all"))
    seen = {json.loads(line)["item_id"] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()} if log.exists() else set()
    kept = len(have)
    spent = 0.0

    def one(index: int, row: dict[str, str]) -> dict[str, Any]:
        item_id = f"sqa-{index}" if sample_index == 1 else f"sqa{sample_index}-{index}"
        question = row["problem"].strip()
        reference = row["answer"].strip()
        with ThreadPoolExecutor(max_workers=5) as pool:
            writer_futs = [pool.submit(_ask, gateway, m, question, temperature=0.3) for m in WRITERS]
            judge_futs = [pool.submit(_ask, gateway, _DEFAULT_JUDGE, question, temperature=0.1)
                          for _ in range(JUDGE_ALONE_ATTEMPTS)]
            answers = [f.result() for f in writer_futs]
            judge_alone = [f.result() for f in judge_futs]
        texts = [a[0] for a in answers]
        correct = [grade(t, reference) for t in texts]
        judge_correct = [grade(t, reference) for t, _ in judge_alone]
        usd = sum((a[1] or 0.0) for a in answers) + sum((j[1] or 0.0) for j in judge_alone)
        keep = (all(texts) and any(correct) and not all(correct) and len(set(texts)) == 3
                and not any(judge_correct))
        return {
            "item_id": item_id, "question": question, "reference": reference, "answers": texts,
            "writers": list(WRITERS), "correct": correct, "judge_alone": [t for t, _ in judge_alone],
            "judge_alone_correct": judge_correct, "keep": keep, "usd": usd,
        }

    todo = [(i, r) for i, r in enumerate(sample)
            if (f"sqa-{i}" if sample_index == 1 else f"sqa{sample_index}-{i}") not in seen]
    print(f"{len(rows)} eligible questions; sample {len(sample)}; {len(todo)} to ask, {len(seen)} asked, {kept} kept")
    batch = max(1, workers) * 4
    with out.open("a", encoding="utf-8") as fh, log.open("a", encoding="utf-8") as lg, ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(todo), batch):
            if kept >= target:
                break  # enough: nothing more is asked, so the sample examined is what is reported
            futures = [pool.submit(one, i, r) for i, r in todo[start:start + batch]]
            for fut in futures:
                try:
                    row = fut.result()
                except Exception as exc:  # noqa: BLE001 — a provider hiccup must not lose the file
                    print(f"  ERROR {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                    continue
                spent += row["usd"]
                lg.write(json.dumps(row, ensure_ascii=False) + "\n")
                lg.flush()
                if row["keep"] and kept < target:
                    kept += 1
                    item = jb.Item(item_id=row["item_id"], question=row["question"], reference=row["reference"],
                                   answers=row["answers"], writers=row["writers"], correct=row["correct"], usd=row["usd"])
                    fh.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
                    fh.flush()
                print(f"  {row['item_id']:<9} writers {''.join('R' if c else 'W' for c in row['correct'])} "
                      f"judge-alone {''.join('R' if c else 'W' for c in row['judge_alone_correct'])} "
                      f"{'KEPT' if row['keep'] else '-'}  kept {kept}  Σ US$ {spent:.4f}", flush=True)
    print(f"{kept} items in {out}")
    return 0


def one(item: Any, arm: str, rotation: int, order: str, seed: int) -> Any:
    """`judge_blind.one`, then the final answer regraded by the text rule."""
    run = jb.one(item, arm, rotation, order, seed)
    run.extracted = answer_line(run.final)
    run.passed = grade(run.final, item.reference)
    return run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--items", default=str(Path(__file__).with_name("results") / "items.jsonl"))
    ap.add_argument("--out", default="")
    ap.add_argument("--target", type=int, default=40)
    ap.add_argument("--cap", type=int, default=SAMPLE_CAP)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--sample", type=int, default=1, help="which slice of the seeded shuffle to collect (addendum 2 = 2)")
    args = ap.parse_args()
    items_path = Path(args.items)
    if args.report:
        print(jb.report(Path(args.report), items_path))
        return 0
    settings = get_settings()
    if not settings.has_any_key():
        print("no provider key in the environment", file=sys.stderr)
        return 2
    if settings.cache:
        print("CHIMERA_CACHE is on; aborting", file=sys.stderr)
        return 2
    items_path.parent.mkdir(parents=True, exist_ok=True)
    if args.collect:
        return collect(items_path, target=args.target, cap=args.cap, workers=args.workers, sample_index=args.sample)
    if not args.run:
        ap.print_help()
        return 1
    # The synthesiser is the judge model (PREREGISTRATION.md): the stage under test is the judge.
    jb._DEFAULT_SYNTHESIZER = _DEFAULT_JUDGE
    items = jb.load_items(items_path)
    if args.limit:
        items = items[: args.limit]
    out = Path(args.out) if args.out else Path(__file__).with_name("results") / f"{time.strftime('%Y-%m-%d')}-runs.jsonl"
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["item_id"], r["arm"], r["rotation"], r["order"]))
    plan = [(item, arm, rot, order, seed) for i, item in enumerate(items) for arm, rot, order, seed in jb.plan_for(item, i)
            if (item.item_id, arm, rot, order) not in done]
    print(f"{len(plan)} runs to make, {len(done)} on disk; judge {_DEFAULT_JUDGE}, synth {jb._DEFAULT_SYNTHESIZER}")
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
                print(f"  {iid:<9} {arm:<6} r{rot} {order:<7} ERROR {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                continue
            spent += r.usd or 0.0
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {iid:<9} {arm:<6} r{rot} {order:<7} {'PASS' if r.passed else 'FAIL'} "
                  f"correct@{r.correct_positions} {r.seconds}s  Σ US$ {spent:.4f}", flush=True)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
