"""How often does a model ask for more than one tool in a turn, and what would parallelising buy?

The census behind `bench/parallel_tools/RESULTS.md`. It reads traces this install has already
written — `StepRecord.tools` is exactly the batch — so it costs nothing and needs no new runs.

Complete rather than sampled, and that is not fastidiousness: a measurement of co-occurrence needs
BOTH members of a pair inside the sample, so a fraction `f` of the corpus undercounts by roughly `f`
and prints a reassuring number. Here the whole file is a second of CPU.

    python scripts/count_tool_batches.py [path/to/traces.jsonl]

Reported per model, never as one average. Two of the models in the corpus that produced the results
file never batched at all while the shipped default batched 23% of the time; an aggregate over those
is a statement about neither.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

#: Tools a concurrent batch could safely hold: nothing written, no command run, no money spent
#: outside the loop's accounting, no live session touched, and nothing that moves the taint ledger.
#: Absent means unsafe, which is the right default for a list a new tool joins without asking.
#:
#: Median milliseconds measured on the repository this ships in — the numbers are what turn a count
#: of batches into a saving, and a batch of two 0.3 ms reads is not a saving.
SAFE_MS = {
    "read_file": 0.3,
    "list_dir": 1.0,
    "grep": 69.8,
    "glob": 23.7,
    "read_document": 5.0,
    "echo": 0.1,
    "tool_list": 0.1,
    "tool_describe": 0.1,
    "todo_write": 0.1,
}


def default_trace() -> Path:
    from chimera.config import get_settings

    return Path(get_settings().home) / "traces.jsonl"


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else default_trace()
    if not path.is_file():
        print(f"no traces at {path} — a run writes them when `trace_path` is set")
        return 1

    runs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    per_model: dict[str, Counter[int]] = defaultdict(Counter)
    saved_ms: dict[str, float] = defaultdict(float)
    shapes: Counter[tuple[str, ...]] = Counter()
    step_ms: list[float] = []

    for run in runs:
        for step in run.get("steps") or []:
            if step.get("elapsed_ms"):
                step_ms.append(float(step["elapsed_ms"]))
            names = [str(t.get("name") or "") for t in (step.get("tools") or [])]
            if not names:
                continue
            model = str(step.get("model") or "?")
            per_model[model][len(names)] += 1
            if len(names) >= 2:
                shapes[tuple(sorted(names))] += 1
                if all(n in SAFE_MS for n in names):
                    # In parallel only the slowest of the batch is waited on.
                    costs = sorted((SAFE_MS[n] for n in names), reverse=True)
                    saved_ms[model] += sum(costs[1:])

    total = sum(sum(c.values()) for c in per_model.values())
    print(f"{len(runs)} runs · {total} steps that called a tool\n")
    print(f"{'model':44} {'steps':>6} {'multi':>6} {'multi %':>8} {'saved ms':>9}")
    for model, counts in sorted(per_model.items(), key=lambda kv: -sum(kv[1].values())):
        steps = sum(counts.values())
        multi = sum(v for k, v in counts.items() if k >= 2)
        print(
            f"{model.replace('openrouter/', '')[:44]:44} {steps:6} {multi:6} "
            f"{multi / steps * 100:7.1f}% {saved_ms[model]:9.1f}"
        )

    print("\nbatches of two or more, by shape:")
    for names, n in shapes.most_common(10):
        mark = "safe " if all(x in SAFE_MS for x in names) else "mixed"
        print(f"  {n:4}x [{mark}] {', '.join(names)}")

    saved = sum(saved_ms.values())
    print(f"\ntotal saved by parallelising every safe batch: {saved:.0f} ms over {len(runs)} runs")
    print(f"per run: {saved / len(runs) if runs else 0:.1f} ms")
    if step_ms:
        median = statistics.median(step_ms)
        share = saved / len(runs) / median * 100 if runs else 0
        print(f"median step (model call + its tools): {median:.0f} ms")
        print(f"the saving, as a share of ONE median step: {share:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
