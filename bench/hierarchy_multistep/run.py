"""Multi-step hierarchy A/B runner — the token-crossover regime (M16 companion).

Both arms on the SAME model, so the comparison isolates context scoping:

- baseline: one growing conversation; all large docs enter on turn 1 and are re-sent
  on every one of the Q sub-question turns.
- scoped: each sub-question routed to a worker seeing ONLY its own document.

Quality = paired McNemar/Wilson (the only place "significant" appears). Tokens =
measured totals per arm; no significance claim on cost. Predictions are registered in
README.md BEFORE running. Writes results/paired.json.

Env: BENCH_MODEL (default deepseek-chat-v3.1), BENCH_TASKS (comma id filter).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/deepseek/deepseek-chat-v3.1")
_ONLY = {t.strip() for t in os.environ.get("BENCH_TASKS", "").split(",") if t.strip()}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    from chimera.eval.hierarchy_ab import ArmOutcome, format_token_report, run_hierarchy_ab
    from chimera.eval.hierarchy_multistep import multistep_tasks, run_baseline, run_scoped
    from chimera.eval.paired import format_report
    from chimera.orchestration.receipts import estimate_tokens
    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    tasks = [t for t in multistep_tasks() if not _ONLY or t.id in _ONLY]
    print(f"model={_MODEL} tasks={len(tasks)} (multi-step, large docs)")

    def complete(messages):  # type: ignore[no-untyped-def]
        result = gateway.complete(messages, model=_MODEL)
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if tokens == 0:
            tokens = estimate_tokens("".join(m["content"] for m in messages) + (result.content or ""))
        # M17/M18: carry provider cache tokens so the arms can report a MEASURED dollar cost.
        return (result.content or "", tokens, result.cache_read_tokens or 0, result.cache_write_tokens or 0)

    cache = {"base_cr": 0, "base_cw": 0, "scoped_cr": 0, "scoped_cw": 0,
             "base_reg": 0, "scoped_reg": 0}

    def baseline(task):  # type: ignore[no-untyped-def]
        run = run_baseline(task, complete)
        cache["base_cr"] += run.cache_read
        cache["base_cw"] += run.cache_write
        cache["base_reg"] += run.tokens - run.cache_read  # non-cached input+output
        return ArmOutcome(passed=run.passed, tokens=run.tokens)

    def treatment(task):  # type: ignore[no-untyped-def]
        run = run_scoped(task, complete)
        cache["scoped_cr"] += run.cache_read
        cache["scoped_cw"] += run.cache_write
        cache["scoped_reg"] += run.tokens - run.cache_read
        return ArmOutcome(passed=run.passed, tokens=run.tokens)

    report = run_hierarchy_ab(
        tasks,
        restore=lambda task: None,
        baseline=baseline,
        treatment=treatment,
        baseline_name="single-context",
        treatment_name="scoped",
    )
    print()
    print(format_report(report.paired))
    print()
    print(format_token_report(report))

    # M18: if the provider reported cache tokens, report the MEASURED dollar reduction
    # (not just the analytic model) by pricing cache reads at 0.1x the model's input rate.
    from chimera.eval.cache_cost import dollar_cost, measured_dollar_reduction
    from chimera.fusion.receipts import resolve_price

    price = resolve_price(_MODEL)
    total_cr = cache["base_cr"] + cache["scoped_cr"]
    if price is not None and total_cr > 0:
        base_usd = dollar_cost(regular_input=cache["base_reg"], output=0, cache_read=cache["base_cr"],
                               input_per_m=price.input_per_m, output_per_m=price.output_per_m)
        scoped_usd = dollar_cost(regular_input=cache["scoped_reg"], output=0, cache_read=cache["scoped_cr"],
                                 input_per_m=price.input_per_m, output_per_m=price.output_per_m)
        print(f"\nMEASURED cache: baseline read={cache['base_cr']} write={cache['base_cw']} | "
              f"scoped read={cache['scoped_cr']} write={cache['scoped_cw']}")
        print(f"MEASURED dollar reduction (cache priced at 0.1x): "
              f"{measured_dollar_reduction(base_usd, scoped_usd):+.1%}  "
              f"(token reduction was {report.summary().get('token_reduction')})")
    else:
        print(f"\nno cache tokens reported by {_MODEL} (cache_read total={total_cr}) — "
              "dollar == token here; caching narrows it only where the provider caches.")

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    payload = {
        "model": _MODEL,
        "task_ids": [t.id for t in tasks],
        "summary": report.summary(),
        "baseline_tokens": report.baseline_tokens,
        "treatment_tokens": report.treatment_tokens,
    }
    (out / "paired.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwritten: {out / 'paired.json'}")


if __name__ == "__main__":
    main()
