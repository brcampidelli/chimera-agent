"""Hierarchy paired A/B runner (M16-A8) — single-agent vs orchestrator-worker, same model.

Per task, BOTH arms run on the same mid model so the comparison isolates the
ORCHESTRATION (context scoping + budgets + contracts), not model strength:

- baseline: ONE call carrying all documents inline + the full question.
- hierarchy: run_prepared — one budgeted worker per document (each sees ONLY its
  own doc), top-tier synthesis over the bounded summaries.

Grading is independent and deterministic (planted-fact substring checks, ALL must
hold). Quality verdict = paired McNemar/Wilson (the only place "significant"
appears); tokens = measured totals per arm, no significance claim on cost.

Env: BENCH_MODEL (mid/worker model), BENCH_TOP_MODEL (synthesis; defaults to
BENCH_MODEL — same family keeps the isolation), BENCH_TASKS (comma ids filter).
Writes results/paired.json; predictions are registered in README.md BEFORE runs.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

from tasks import baseline_prompt, make_specs, synthetic_tasks  # noqa: E402

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/deepseek/deepseek-chat-v3.1")
_TOP = os.environ.get("BENCH_TOP_MODEL", _MODEL)
_ONLY = {t.strip() for t in os.environ.get("BENCH_TASKS", "").split(",") if t.strip()}


def main() -> None:
    from chimera.eval.hierarchy_ab import ArmOutcome, format_token_report, run_hierarchy_ab
    from chimera.eval.paired import format_report
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.envelope_verify import EnvelopeVerifier
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
    from chimera.orchestration.receipts import estimate_tokens
    from chimera.providers import LLMGateway

    tasks = [t for t in synthetic_tasks() if not _ONLY or t.id in _ONLY]
    gateway = LLMGateway()
    workdir = Path(tempfile.mkdtemp(prefix="hierarchy-ab-"))
    print(f"model={_MODEL} top={_TOP} tasks={len(tasks)} artifacts={workdir}")

    def baseline(task):  # type: ignore[no-untyped-def]
        prompt = baseline_prompt(task)
        result = gateway.complete([{"role": "user", "content": prompt}], model=_MODEL)
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if tokens == 0:  # provider reported nothing — estimate, and say so in the row
            tokens = estimate_tokens(prompt + (result.content or ""))
        return ArmOutcome(passed=task.check(result.content or ""), tokens=tokens)

    def treatment(task):  # type: ignore[no-untyped-def]
        store = ArtifactStore(workdir / task.id)
        orchestrator = HierarchicalOrchestrator(
            gateway,
            weak_model=_MODEL,
            mid_model=_MODEL,
            top_model=_TOP,
            store=store,
            verifier=EnvelopeVerifier(store=store, backend=None, spot_rate=0.0),
            config=HierarchyConfig(max_workers=4, fuse_final=False),
        )
        result = orchestrator.run_prepared(task.question, make_specs(task))
        return ArmOutcome(passed=task.check(result.answer or ""), tokens=result.total_tokens)

    report = run_hierarchy_ab(
        tasks, restore=lambda task: None, baseline=baseline, treatment=treatment
    )
    print()
    print(format_report(report.paired))
    print()
    print(format_token_report(report))

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    payload = {
        "model": _MODEL,
        "top_model": _TOP,
        "task_ids": [t.id for t in tasks],
        "summary": report.summary(),
        "baseline_tokens": report.baseline_tokens,
        "treatment_tokens": report.treatment_tokens,
    }
    (out / "paired.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwritten: {out / 'paired.json'}")


if __name__ == "__main__":
    main()
