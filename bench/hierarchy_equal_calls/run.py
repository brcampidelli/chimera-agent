"""At an equal number of calls, does the hierarchy beat one agent that re-reads?
Registered in `PREREGISTRATION.md` before any model call.

Four arms on one weak backbone, three runs per task, the ten read-heavy tasks of `bench/hierarchy`.
`single_equal` and `hierarchy` make exactly D + 1 calls per task, D the document count.

    python bench/hierarchy_equal_calls/run.py --out bench/hierarchy_equal_calls/results/<tag>.jsonl
    python bench/hierarchy_equal_calls/run.py --report bench/hierarchy_equal_calls/results/<tag>.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.config import get_settings  # noqa: E402
from chimera.eval.hierarchy_ab import (  # noqa: E402
    HierarchyTask,
    baseline_prompt,
    make_specs,
    synthetic_tasks,
)
from chimera.eval.replicated import (  # noqa: E402
    ReplicatedArm,
    compare_replicated,
    format_replicated_report,
)
from chimera.orchestration.receipts import price_completion  # noqa: E402

BACKBONE = "openrouter/meta-llama/llama-3.1-8b-instruct"  # overridden by --backbone; see PREREGISTRATION amendment 2
#: The synthesiser's model when it is not the backbone (`--synth-backbone`; addendum 2). Empty = the
#: backbone on every role, which is every run before the addendum.
SYNTH_BACKBONE = ""
ARMS = ("single_1", "single_equal", "hierarchy", "hierarchy_no_synth", "hierarchy_verbatim")
TEMPERATURE = 0.3
REFINE = (
    "Here is your previous answer:\n\n{previous}\n\nRe-read the documents above and revise the "
    "answer so that every part of the question is answered with the exact values from the "
    "documents. Reply with the complete revised answer only."
)


def _retrying(call: Any, *, tries: int = 6, wait: float = 20.0) -> Any:
    for attempt in range(tries):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — only the rate-limit shape is retried
            text = str(exc)
            if attempt == tries - 1 or ("429" not in text and "rate-limit" not in text.lower()):
                raise
            time.sleep(wait * (attempt + 1))
    raise RuntimeError("unreachable")


class _Metered:
    """A backend that counts calls and prices them, so every arm's cost is what it spent."""

    def __init__(self, inner: Any, model: str, synth_model: str = "") -> None:
        self.inner, self.model, self.synth_model = inner, model, synth_model
        self.calls, self.usd, self.tokens, self.unpriced = 0, 0.0, 0, False
        # The synthesis call on its own, when it runs on another model (addendum 2): its completion
        # tokens are what the verbatim sentence can change, and its price is the top tier's.
        self.synth_calls, self.synth_completion_tokens, self.synth_usd = 0, 0, 0.0

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        # The backbone is frozen on every role and every arm — except the synthesiser when the run
        # asked for one (`--synth-backbone`), which the orchestrator requests by its `top_model`.
        is_synth = bool(self.synth_model) and kwargs.get("model") == self.synth_model
        kwargs["model"] = self.synth_model if is_synth else self.model
        kwargs.setdefault("temperature", TEMPERATURE)
        result = _retrying(lambda: self.inner.complete(messages, **kwargs))
        self.calls += 1
        cost = price_completion(result)
        self.usd += cost.usd
        self.unpriced = self.unpriced or cost.unpriced is not None
        self.tokens += (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if is_synth:
            self.synth_calls += 1
            self.synth_completion_tokens += result.completion_tokens or 0
            self.synth_usd += cost.usd
        return result


def planted_facts(task: HierarchyTask) -> list[str]:
    """The `- fact` lines under `## Key items` in each document — the facts the grader is about."""
    facts: list[str] = []
    for content in task.docs.values():
        in_items = False
        for line in content.splitlines():
            if line.startswith("## Key items"):
                in_items = True
            elif in_items and line.startswith("- "):
                facts.append(line[2:].strip())
            elif in_items and line.startswith("#"):
                in_items = False
    return facts


def _tokens_of(fact: str) -> set[str]:
    """What an answer must carry to have reported this fact: its last word and its last figure.

    `hierarchy_ab.check` wants the first figure and the word after it, verbatim — "3.1 requires" for
    "Alpha 3.1 requires Python 3.12" — which grades the phrasing rather than the reading: the pilot's
    single call answered "**Alpha 3.1** · Requires: Python 3.12" and failed every needle. The mid
    model happened to write the sentence back verbatim; an 8B model does not, and a grader that
    measures that difference is measuring style (§2l). Two tokens per fact, both values.
    """
    words = [w.strip(".,;:*`") for w in fact.split()]
    out = {words[-1].lower()}
    digits = [w for w in words if any(ch.isdigit() for ch in w)]
    if digits:
        out.add(digits[-1].lower())
    return {w for w in out if w}


def value_check(task: HierarchyTask, answer: str) -> bool:
    """Every planted fact's value tokens appear in the answer. Robust to formatting, strict on values."""
    low = answer.lower()
    return all(all(tok in low for tok in _tokens_of(fact)) for fact in planted_facts(task))


@dataclass
class Trial:
    task_id: str
    arm: str
    rep: int
    backbone: str
    docs: int
    calls: int
    passed: bool
    passed_verbatim: bool
    """`hierarchy_ab.check` — the published grader, reported beside the value grader, never used."""
    answer: str
    tokens: int
    usd: float | None
    seconds: float
    synth_backbone: str = ""
    """The synthesiser's model when it was not the backbone (addendum 2); "" before that."""
    synth_completion_tokens: int = 0
    synth_usd: float = 0.0


def _single(task: HierarchyTask, backend: _Metered, *, refine_rounds: int) -> str:
    messages: list[dict[str, str]] = [{"role": "user", "content": baseline_prompt(task)}]
    answer = (backend.complete(messages).content or "").strip()
    for _ in range(refine_rounds):
        messages = messages + [
            {"role": "assistant", "content": answer},
            {"role": "user", "content": REFINE.format(previous=answer)},
        ]
        answer = (backend.complete(messages).content or "").strip()
    return answer


def _hierarchy(
    task: HierarchyTask, backend: _Metered, *, synth: bool, workdir: Path, verbatim: bool = False
) -> str:
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.envelope_verify import EnvelopeVerifier
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig

    store = ArtifactStore(workdir / task.id)
    orchestrator = HierarchicalOrchestrator(
        backend,
        weak_model=BACKBONE, mid_model=BACKBONE, top_model=SYNTH_BACKBONE or BACKBONE,
        store=store,
        verifier=EnvelopeVerifier(store=store, backend=None, spot_rate=0.0),
        config=HierarchyConfig(
            max_workers=4, fuse_final=False, spot_rate=0.0, synthesis_verbatim=verbatim
        ),
    )
    if synth:
        return orchestrator.run_prepared(task.question, make_specs(task)).answer or ""
    # Leave-one-in: the workers alone. `_dispatch` is the worker fan-out the full arm uses; the
    # summaries are joined the way `_synthesize` would have read them, and that text is graded.
    envelopes, _receipts = orchestrator._dispatch(make_specs(task))
    return "\n\n".join(f"### {env.task_id}\n{env.summary}" for env in envelopes)


def one(task: HierarchyTask, arm: str, rep: int, *, workdir: Path) -> Trial:
    from chimera.providers import LLMGateway

    backend = _Metered(LLMGateway(), BACKBONE, SYNTH_BACKBONE)
    docs = len(task.docs)
    t0 = time.monotonic()
    if arm == "single_1":
        answer = _single(task, backend, refine_rounds=0)
    elif arm == "single_equal":
        answer = _single(task, backend, refine_rounds=docs)
    elif arm == "hierarchy":
        # The prompt as it was before the verbatim sentence became the default (see RESULTS.md).
        answer = _hierarchy(task, backend, synth=True, workdir=workdir, verbatim=False)
    elif arm == "hierarchy_verbatim":
        # The follow-up RESULTS.md named: the same D + 1 calls, the synthesis asked for the figures.
        answer = _hierarchy(task, backend, synth=True, workdir=workdir, verbatim=True)
    else:
        answer = _hierarchy(task, backend, synth=False, workdir=workdir)
    return Trial(
        task_id=task.id, arm=arm, rep=rep, backbone=BACKBONE, docs=docs, calls=backend.calls,
        passed=value_check(task, answer),
        passed_verbatim=task.check(answer),
        answer=answer, tokens=backend.tokens, usd=(None if backend.unpriced else backend.usd),
        seconds=round(time.monotonic() - t0, 1),
        synth_backbone=SYNTH_BACKBONE, synth_completion_tokens=backend.synth_completion_tokens,
        synth_usd=backend.synth_usd,
    )


def _arm(rows: list[dict[str, Any]], name: str, task_ids: list[str], reps: int) -> ReplicatedArm:
    grid = []
    for tid in task_ids:
        mine = {r["rep"]: r["passed"] for r in rows if r["arm"] == name and r["task_id"] == tid}
        grid.append([bool(mine.get(i, False)) for i in range(reps)])
    return ReplicatedArm(name, grid)


def report(path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    task_ids = sorted({r["task_id"] for r in rows})
    reps = max(r["rep"] for r in rows) + 1
    arms = {name: _arm(rows, name, task_ids, reps) for name in ARMS if any(r["arm"] == name for r in rows)}
    backbone = rows[0].get("backbone", BACKBONE)
    synth_backbone = rows[0].get("synth_backbone", "")
    lines = [f"# hierarchy_equal_calls — {len(rows)} trials, backbone {backbone}"
             + (f", synthesiser {synth_backbone}" if synth_backbone else "")
             + f", US$ {sum(r['usd'] or 0 for r in rows):.4f}", ""]
    lines.append("| arm | calls/task (mean) | pass@1 | pass^k | flip rate | tokens/task (mean) | US$ |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for name, arm in arms.items():
        mine = [r for r in rows if r["arm"] == name]
        lines.append(f"| `{name}` | {sum(r['calls'] for r in mine) / len(mine):.1f} | {arm.pass_at_1:.2f} | "
                     f"{arm.pass_pow_k:.2f} | {arm.flip_rate:.2f} | {sum(r['tokens'] for r in mine) / len(mine):,.0f} | "
                     f"{sum(r['usd'] or 0 for r in mine):.4f} |")
    lines.append("")
    if synth_backbone:
        lines.append("| arm | synthesis completion tokens (mean) | synthesis US$ (total) |")
        lines.append("|---|---:|---:|")
        for name in arms:
            mine = [r for r in rows if r["arm"] == name and r.get("synth_backbone")]
            if mine:
                lines.append(f"| `{name}` | {sum(r.get('synth_completion_tokens', 0) for r in mine) / len(mine):,.0f} | "
                             f"{sum(r.get('synth_usd', 0.0) for r in mine):.4f} |")
        lines.append("")
    single = arms.get("single_1")
    if single is not None:
        lines.append(f"- instrument: `single_1` pass@1 = {single.pass_at_1:.2f} "
                     f"({'inside' if 0.2 <= single.pass_at_1 <= 0.85 else 'OUTSIDE'} the registered 20–85% band)")
        lines.append("")
    for base, treat, label in (
        ("single_equal", "hierarchy", "PRIMARY — hierarchy vs one agent given the same calls"),
        ("single_1", "single_equal", "does re-reading help at all"),
        ("hierarchy_no_synth", "hierarchy", "leave-one-in: the synthesiser"),
        ("single_1", "hierarchy", "the comparison the existing benches make, off the ceiling"),
        ("hierarchy", "hierarchy_verbatim", "follow-up: the synthesis asked to carry the figures verbatim"),
        ("hierarchy_no_synth", "hierarchy_verbatim", "follow-up: verbatim synthesis vs the workers alone"),
    ):
        if base in arms and treat in arms:
            lines.append(f"## {label}")
            lines.append("")
            lines.append("```")
            lines.append(format_replicated_report(compare_replicated(arms[base], arms[treat])))
            lines.append("```")
            lines.append("")
    per_task: dict[str, dict[str, str]] = defaultdict(dict)
    for name, arm in arms.items():
        for tid, row in zip(task_ids, arm.runs, strict=True):
            per_task[tid][name] = "".join("P" if v else "f" for v in row)
    lines.append("| task | " + " | ".join(f"`{n}`" for n in arms) + " |")
    lines.append("|---|" + "---|" * len(arms))
    for tid in task_ids:
        lines.append(f"| {tid} | " + " | ".join(per_task[tid].get(n, "") for n in arms) + " |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--backbone", default="")
    ap.add_argument("--synth-backbone", default="", help="the synthesiser's model, when not the backbone (addendum 2)")
    args = ap.parse_args()
    global BACKBONE, SYNTH_BACKBONE
    if args.backbone:
        BACKBONE = args.backbone
    if args.synth_backbone:
        SYNTH_BACKBONE = args.synth_backbone
    if args.report:
        print(report(Path(args.report)))
        return 0
    settings = get_settings()
    if not settings.has_any_key():
        print("no provider key in the environment", file=sys.stderr)
        return 2
    if settings.cache:
        print("CHIMERA_CACHE is on; aborting", file=sys.stderr)
        return 2
    tasks = synthetic_tasks()
    if args.limit:
        tasks = tasks[: args.limit]
    out = Path(args.out) if args.out else Path(__file__).with_name("results") / f"{time.strftime('%Y-%m-%d')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["task_id"], r["arm"], r["rep"]))
    plan = [(t, arm, rep) for rep in range(args.reps) for t in tasks for arm in args.arms.split(",")
            if (t.id, arm, rep) not in done]
    workdir = Path(tempfile.mkdtemp(prefix="equal-calls-"))
    print(f"backbone {BACKBONE}; synthesiser {SYNTH_BACKBONE or BACKBONE}; {len(plan)} trials to run, {len(done)} on disk")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    spent = 0.0
    with out.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, t, arm, rep, workdir=workdir): (t.id, arm, rep) for t, arm, rep in plan}
        for fut in as_completed(futures):
            tid, arm, rep = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001 — a provider hiccup must not lose the file
                print(f"  {tid:<16} {arm:<18} r{rep} ERROR {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                continue
            spent += r.usd or 0.0
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {tid:<16} {arm:<18} r{rep} {'PASS' if r.passed else 'FAIL'} calls={r.calls} "
                  f"tokens={r.tokens} {r.seconds}s  Σ US$ {spent:.4f}", flush=True)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
