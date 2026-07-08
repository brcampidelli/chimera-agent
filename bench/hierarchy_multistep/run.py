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
        return (result.content or "", tokens)

    def baseline(task):  # type: ignore[no-untyped-def]
        run = run_baseline(task, complete)
        return ArmOutcome(passed=run.passed, tokens=run.tokens)

    def treatment(task):  # type: ignore[no-untyped-def]
        run = run_scoped(task, complete)
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
    print("\nnote: token counts are real; a provider with prompt caching bills the")
    print("baseline's repeated doc prefix at ~0.1x, so the DOLLAR gap is narrower.")

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
