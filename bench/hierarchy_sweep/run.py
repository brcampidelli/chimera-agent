"""Token-crossover sweep — the hierarchy's saving across THREE axes (M16 companion).

`bench/hierarchy` (single-shot) showed a LOSS; `bench/hierarchy_multistep` showed a WIN.
This sweeps the knobs that drive the effect and measures where the saving lands:

- **D** (BENCH_AXIS=D): number of documents. Saving ~ (D-1)/D — isolation scales with
  how many docs a single agent must carry. (default)
- **S** (BENCH_AXIS=S): document size (filler reps). Tiny docs -> the fixed per-call
  framing is a bigger slice, so a smaller win; big docs -> win -> (D-1)/D. Rises with S,
  does not invert here (the loss regime is the separate single-shot bench).
- **Q** (BENCH_AXIS=Q): conversation length. Both arms scale ~linearly in Q, so the win
  is roughly flat — a stability check that the win holds as sessions get long.

Both arms use the SAME model (isolates context scoping). Deterministic planted-needle
grading. Tokens are measured totals; no significance claim on cost. Writes results/<axis>.json.

Env: BENCH_MODEL (default deepseek-chat-v3.1), BENCH_AXIS (D|S|Q, default D),
BENCH_POINTS (comma list; defaults per axis).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/deepseek/deepseek-chat-v3.1")
_AXIS = os.environ.get("BENCH_AXIS", "D").upper()
_DEFAULT_POINTS = {"D": "2,3,4,5", "S": "4,10,20,40,80", "Q": "2,3,4,6"}
_POINTS = [int(x) for x in os.environ.get("BENCH_POINTS", _DEFAULT_POINTS.get(_AXIS, "")).split(",") if x.strip()]

# Fixed values for the axes NOT being swept.
_FIXED_D = int(os.environ.get("BENCH_FIXED_D", "3"))
_FIXED_REPS = int(os.environ.get("BENCH_FIXED_REPS", "40"))


def _task_for(axis: str, p: int):  # type: ignore[no-untyped-def]
    from chimera.eval.hierarchy_multistep import make_sweep_task

    if axis == "D":
        return p, make_sweep_task(p)  # Q = D
    if axis == "S":
        return p, make_sweep_task(_FIXED_D, filler_reps=p)
    if axis == "Q":
        return p, make_sweep_task(_FIXED_D, num_steps=p)
    raise SystemExit(f"unknown BENCH_AXIS={axis!r} (use D, S or Q)")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    from chimera.eval.hierarchy_multistep import run_baseline, run_scoped
    from chimera.orchestration.receipts import estimate_tokens
    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    print(f"model={_MODEL} axis={_AXIS} points={_POINTS} (fixed D={_FIXED_D}, reps={_FIXED_REPS})")

    def complete(messages):  # type: ignore[no-untyped-def]
        result = gateway.complete(messages, model=_MODEL)
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if tokens == 0:
            tokens = estimate_tokens("".join(m["content"] for m in messages) + (result.content or ""))
        return (result.content or "", tokens)

    rows = []
    print(f"\n{_AXIS:>4}  {'baseline':>9}  {'scoped':>9}  {'reduction':>9}  quality")
    for p in _POINTS:
        label, task = _task_for(_AXIS, p)
        base = run_baseline(task, complete)
        scoped = run_scoped(task, complete)
        reduction = 1 - scoped.tokens / base.tokens if base.tokens else 0.0
        ok = base.passed and scoped.passed
        rows.append({
            "axis": _AXIS, "point": label, "baseline_tokens": base.tokens,
            "scoped_tokens": scoped.tokens, "reduction": round(reduction, 4),
            "baseline_passed": base.passed, "scoped_passed": scoped.passed,
        })
        print(f"{label:>4}  {base.tokens:>9}  {scoped.tokens:>9}  {reduction:>+8.1%}  "
              f"{'ok' if ok else 'FAIL'}")

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{_AXIS.lower()}.json").write_text(
        json.dumps({"model": _MODEL, "axis": _AXIS, "rows": rows}, indent=2), encoding="utf-8"
    )
    print(f"\nwritten: {out / (_AXIS.lower() + '.json')}")
    print("note: token counts are real; prompt caching narrows the DOLLAR gap.")


if __name__ == "__main__":
    main()
