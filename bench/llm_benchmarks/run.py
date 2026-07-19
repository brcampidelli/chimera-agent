"""Run the pre-registered ladder: {HumanEval, GSM8K} x {weak, mid}, paired, under a hard budget cap.

    python bench/llm_benchmarks/run.py --suite humaneval --tier weak
    python bench/llm_benchmarks/run.py --suite gsm8k --tier mid --limit 100

Two properties that matter more than convenience:

* **Hard budget stop.** The run aborts before starting a pair that would cross ``--budget`` (default
  US$1.00, the approved cap). It stops between pairs, never mid-pair, so the paired design is never
  left with a half-observed item.
* **Resumable, never re-rolled.** Completed pairs are journalled; a resumed run skips them. A pair is
  observed exactly once — re-running a disappointing pair until it improves is p-hacking.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))  # sibling modules (arms/datasets/humaneval/gsm8k)

from arms import Spend  # noqa: E402

from chimera.eval.paired import compare_paired, format_report  # noqa: E402

RESULTS = Path(__file__).parent / "results"

TIERS = {
    "weak": "openrouter/mistralai/mistral-small-3.2-24b-instruct",
    "mid": "openrouter/deepseek/deepseek-chat-v3.1",
}


def _journal_path(suite: str, tier: str) -> Path:
    return RESULTS / f"{suite}_{tier}_journal.jsonl"


def _load_journal(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    done: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            done[str(record["id"])] = record
    return done


def _append_journal(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["humaneval", "gsm8k"], required=True)
    parser.add_argument("--tier", choices=sorted(TIERS), required=True)
    parser.add_argument("--limit", type=int, default=0, help="0 = the whole suite")
    parser.add_argument("--budget", type=float, default=1.00, help="hard stop, US$")
    parser.add_argument("--seed", type=int, default=20260719, help="sampling seed (pre-registered)")
    args = parser.parse_args()

    model = TIERS[args.tier]
    spend = Spend()
    journal = _journal_path(args.suite, args.tier)
    done = _load_journal(journal)

    if args.suite == "humaneval":
        import humaneval as suite
        from datasets import load_humaneval

        problems = load_humaneval()
        key = "task_id"
    else:
        import gsm8k as suite  # type: ignore[no-redef]
        from datasets import load_gsm8k

        problems = load_gsm8k()
        random.Random(args.seed).shuffle(problems)  # fixed seed: the sample is reproducible
        key = ""

    if args.limit:
        problems = problems[: args.limit]

    print(f"suite={args.suite} tier={args.tier} model={model} n={len(problems)} budget=${args.budget:.2f}")
    print(f"resuming: {len(done)} pairs already observed\n")

    baseline_results: list[bool] = []
    treatment_results: list[bool] = []
    stopped_early = False

    with tempfile.TemporaryDirectory(prefix="chimera_bench_") as tmp:
        root = Path(tmp)
        for index, problem in enumerate(problems):
            item_id = str(problem[key]) if key else f"gsm8k_{index:04d}"

            if item_id in done:
                record = done[item_id]
                baseline_results.append(bool(record["baseline"]))
                treatment_results.append(bool(record["chimera"]))
                continue

            # Stop BETWEEN pairs, so the paired design never has a half-observed item.
            if spend.usd >= args.budget:
                print(f"\n!! budget cap reached (${spend.usd:.3f} >= ${args.budget:.2f}) — stopping")
                stopped_early = True
                break

            if args.suite == "humaneval":
                base = suite.run_baseline_task(problem, model=model, spend=spend)
                treat = suite.run_chimera_task(problem, model=model, spend=spend, root=root)
            else:
                base = suite.run_baseline_task(problem, model=model, spend=spend)
                treat = suite.run_chimera_task(
                    problem, model=model, spend=spend, root=root, index=index
                )

            baseline_results.append(base)
            treatment_results.append(treat)
            _append_journal(
                journal,
                {"id": item_id, "baseline": base, "chimera": treat, "usd_after": round(spend.usd, 4)},
            )
            flag = {(False, True): "  +GAIN", (True, False): "  -LOSS"}.get((base, treat), "")
            print(
                f"[{index + 1}/{len(problems)}] {item_id}: "
                f"base={'PASS' if base else 'FAIL'} chimera={'PASS' if treat else 'FAIL'}"
                f"  ${spend.usd:.3f}{flag}"
            )

    if not baseline_results:
        print("no pairs observed")
        return 1

    result = compare_paired(
        baseline_results, treatment_results, baseline_name="baseline", treatment_name="chimera"
    )
    print("\n" + format_report(result))
    print(
        f"\nspend: ${spend.usd:.3f} "
        f"({spend.prompt_tokens:,} prompt + {spend.completion_tokens:,} completion tokens)"
    )
    if spend.estimated_calls:
        print(
            f"NOTE: {spend.estimated_calls} calls had estimated (not measured) cost — "
            "the loop runs in a subprocess. Treat the total as an order-of-magnitude figure."
        )
    if stopped_early:
        print("NOTE: stopped at the budget cap — this is a partial suite, not the full n.")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{args.suite}_{args.tier}.json"
    out.write_text(
        json.dumps(
            {
                "suite": args.suite,
                "tier": args.tier,
                "model": model,
                "n": len(baseline_results),
                "planned_n": len(problems),
                "stopped_at_budget": stopped_early,
                "summary": result.summary(),
                "spend_usd": round(spend.usd, 4),
                "spend_is_partly_estimated": bool(spend.estimated_calls),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
