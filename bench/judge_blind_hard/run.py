"""On problems the judge cannot solve alone, does its verdict follow the name or the position?
Registered in `PREREGISTRATION.md` before any model call. The follow-up `bench/judge_blind` named.

Same pipeline, same design, same report as `bench/judge_blind` — imported from there — with a corpus
the judge cannot solve: AIME problems on which the production judge, asked twice, was wrong twice.

    python bench/judge_blind_hard/run.py --collect
    python bench/judge_blind_hard/run.py --run
    python bench/judge_blind_hard/run.py --report bench/judge_blind_hard/results/<tag>.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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
    """`bench/judge_blind/run.py` by path — the pipeline, the design, the report. By path and not by
    name, because both files are called `run.py` and a bare `import run` resolves to whichever
    directory sits first on the path, which was this one."""
    import importlib.util

    path = REPO / "bench" / "judge_blind" / "run.py"
    spec = importlib.util.spec_from_file_location("judge_blind_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["judge_blind_run"] = module  # dataclasses need the module registered before exec
    spec.loader.exec_module(module)
    return module


jb = _load_judge_blind()

DATASET = "AI-MO/aimo-validation-aime"  # Apache-2.0; 90 problems, integer answers
ROWS_URL = (
    "https://datasets-server.huggingface.co/rows?dataset=AI-MO%2Faimo-validation-aime"
    "&config=default&split=train&offset={offset}&length=100"
)
WRITERS = (
    "openrouter/z-ai/glm-5.3-flash",
    "openrouter/openai/gpt-oss-20b",
    "openrouter/moonshotai/kimi-k2",
)
WRITER_MAX_TOKENS = 16_000
JUDGE_ALONE_ATTEMPTS = 2


def load_aime() -> list[dict[str, Any]]:
    """The 90 problems, from the datasets-server API. Not vendored: the licence is the dataset's."""
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        with urllib.request.urlopen(ROWS_URL.format(offset=offset), timeout=60) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        rows = [r["row"] for r in data.get("rows", [])]
        out.extend(rows)
        if not rows or len(out) >= int(data.get("num_rows_total", 0)):
            break
        offset += len(rows)
    return out


def _ask(gateway: Any, model: str, question: str, *, temperature: float, max_tokens: int) -> tuple[str, float | None]:
    result = jb._retrying(lambda: gateway.complete(
        [{"role": "user", "content": question + jb.SUFFIX}], model=model, temperature=temperature,
        max_tokens=max_tokens,
    ))
    cost = price_completion(result)
    return (result.content or "").strip(), (None if cost.unpriced else cost.usd)


def collect(out: Path, *, workers: int) -> int:
    from concurrent.futures import ThreadPoolExecutor

    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    problems = load_aime()
    have = {jb.Item(**json.loads(line)).item_id for line in out.read_text(encoding="utf-8").splitlines() if line.strip()} if out.exists() else set()
    log = out.with_name("collect-all.jsonl")  # every problem's writer + judge-alone verdicts, kept or not
    seen = {json.loads(line)["item_id"] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()} if log.exists() else set()
    kept = len(have)
    spent = 0.0

    def one(problem: dict[str, Any]) -> dict[str, Any]:
        item_id = f"aime-{problem['id']}"
        question = str(problem["problem"])
        reference = jb.normalise(str(problem["answer"]))
        with ThreadPoolExecutor(max_workers=5) as pool:
            writer_futs = [pool.submit(_ask, gateway, m, question, temperature=0.3, max_tokens=WRITER_MAX_TOKENS) for m in WRITERS]
            judge_futs = [pool.submit(_ask, gateway, _DEFAULT_JUDGE, question, temperature=0.1, max_tokens=WRITER_MAX_TOKENS)
                          for _ in range(JUDGE_ALONE_ATTEMPTS)]
            answers = [f.result() for f in writer_futs]
            judge_alone = [f.result() for f in judge_futs]
        texts = [a[0] for a in answers]
        correct = [jb.extract_answer(t) == reference for t in texts]
        judge_correct = [jb.extract_answer(t) == reference for t, _ in judge_alone]
        usd = sum((a[1] or 0.0) for a in answers) + sum((j[1] or 0.0) for j in judge_alone)
        keep = (all(texts) and any(correct) and not all(correct) and len(set(texts)) == 3
                and not any(judge_correct))
        return {
            "item_id": item_id, "question": question, "reference": reference, "answers": texts,
            "writers": list(WRITERS), "correct": correct, "judge_alone_correct": judge_correct,
            "keep": keep, "usd": usd,
        }

    todo = [p for p in problems if f"aime-{p['id']}" not in seen]
    print(f"{len(problems)} problems; {len(todo)} to ask, {len(seen)} already asked, {kept} kept so far")
    with out.open("a", encoding="utf-8") as fh, log.open("a", encoding="utf-8") as lg, ThreadPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(one, todo):
            spent += row["usd"]
            lg.write(json.dumps(row, ensure_ascii=False) + "\n")
            lg.flush()
            if row["keep"]:
                kept += 1
                item = jb.Item(item_id=row["item_id"], question=row["question"], reference=row["reference"],
                               answers=row["answers"], writers=row["writers"], correct=row["correct"], usd=row["usd"])
                fh.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
                fh.flush()
            print(f"  {row['item_id']:<10} writers {''.join('R' if c else 'W' for c in row['correct'])} "
                  f"judge-alone {''.join('R' if c else 'W' for c in row['judge_alone_correct'])} "
                  f"{'KEPT' if row['keep'] else '-'}  Σ US$ {spent:.4f}", flush=True)
    print(f"{kept} items in {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--items", default=str(Path(__file__).with_name("results") / "items.jsonl"))
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
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
        return collect(items_path, workers=args.workers)
    if not args.run:
        ap.print_help()
        return 1
    # The synthesiser is the judge model here (see PREREGISTRATION.md: amended for cost; the stage
    # under test is the judge, and the synthesiser is held fixed across arms either way).
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
        futures = {pool.submit(jb.one, item, arm, rot, order, seed): (item.item_id, arm, rot, order)
                   for item, arm, rot, order, seed in plan}
        for fut in as_completed(futures):
            iid, arm, rot, order = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001 — a provider hiccup must not lose the file
                print(f"  {iid:<10} {arm:<6} r{rot} {order:<7} ERROR {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                continue
            spent += r.usd or 0.0
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {iid:<10} {arm:<6} r{rot} {order:<7} {'PASS' if r.passed else 'FAIL'} "
                  f"correct@{r.correct_positions} {r.seconds}s  Σ US$ {spent:.4f}", flush=True)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
