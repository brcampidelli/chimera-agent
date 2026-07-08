"""Token-crossover sweep — how the hierarchy's token saving scales with D (M16 companion).

The single-shot bench showed a LOSS, the multi-step bench a WIN. This sweep varies the
one knob that drives the effect — the number of documents D a single agent would have
to juggle — and measures where the saving lands. Same model both arms.

- baseline (single context): one growing conversation over all D docs; every one of
  the D sub-question turns re-sends all D docs -> ~ D * (D * doc).
- scoped (hierarchy): each sub-question routed to a worker seeing ONLY its doc -> ~ D * doc.
- Predicted saving ~ (D-1)/D: 50% at D=2, 67% at D=3, 75% at D=4, 80% at D=5.

Grading is deterministic (planted needles, ALL must hold). Tokens are measured totals;
no significance claim on cost. Writes results/sweep.json.

Env: BENCH_MODEL (default deepseek-chat-v3.1), BENCH_DS (comma list, default 2,3,4,5).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/deepseek/deepseek-chat-v3.1")
_DS = [int(x) for x in os.environ.get("BENCH_DS", "2,3,4,5").split(",") if x.strip()]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    from chimera.eval.hierarchy_multistep import make_sweep_task, run_baseline, run_scoped
    from chimera.orchestration.receipts import estimate_tokens
    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    print(f"model={_MODEL} D sweep={_DS}")

    def complete(messages):  # type: ignore[no-untyped-def]
        result = gateway.complete(messages, model=_MODEL)
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if tokens == 0:
            tokens = estimate_tokens("".join(m["content"] for m in messages) + (result.content or ""))
        return (result.content or "", tokens)

    rows = []
    print(f"\n{'D':>3}  {'baseline':>9}  {'scoped':>9}  {'reduction':>9}  {'(D-1)/D':>8}  quality")
    for d in _DS:
        task = make_sweep_task(d)
        base = run_baseline(task, complete)
        scoped = run_scoped(task, complete)
        reduction = 1 - scoped.tokens / base.tokens if base.tokens else 0.0
        predicted = (d - 1) / d
        ok = base.passed and scoped.passed
        rows.append({
            "d": d, "baseline_tokens": base.tokens, "scoped_tokens": scoped.tokens,
            "reduction": round(reduction, 4), "predicted": round(predicted, 4),
            "baseline_passed": base.passed, "scoped_passed": scoped.passed,
        })
        print(f"{d:>3}  {base.tokens:>9}  {scoped.tokens:>9}  {reduction:>+8.1%}  "
              f"{predicted:>7.1%}  {'ok' if ok else 'FAIL'}")

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "sweep.json").write_text(
        json.dumps({"model": _MODEL, "rows": rows}, indent=2), encoding="utf-8"
    )
    print(f"\nwritten: {out / 'sweep.json'}")
    print("note: token counts are real; prompt caching narrows the DOLLAR gap.")


if __name__ == "__main__":
    main()
