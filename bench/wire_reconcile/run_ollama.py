"""S30-61, the local-Ollama replication: real runs with the wire log on, then mutated steplog copies.

    uv run python bench/wire_reconcile/run_ollama.py generate --out bench/wire_reconcile/results/ollama
    uv run python bench/wire_reconcile/run_ollama.py inject --runs bench/wire_reconcile/results/ollama

See PREREGISTRATION.md, Amendment 1. ``generate`` makes the pristine runs (one CHIMERA_HOME per run,
holding the gateway's ``wire.jsonl`` and the agent's ``traces.jsonl``) and is the only step that calls a
model. ``inject`` never calls one: it copies each run four times (clean, omission, fabrication, altered
copy), mutates the copy's steplog only, and reconciles it against the untouched wire log.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from chimera.eval import proportions
from chimera.governance.reconcile import digest, reconcile

MODEL = "ollama_chat/qwen3:4b"
NUM_CTX = 16384
MAX_STEPS = 4
REPLICAS = 10
SEED = 3061
CLASSES = ("clean", "omission", "fabrication", "altered copy")
#: Amendment 1 §1: ten fixed file tasks in an empty workspace; no shell, no network.
TASKS = (
    "Create a file notes.txt containing the line 'alpha', then read it back and tell me what it says.",
    "List the files in the current folder, then create todo.md with a heading '# Todo'.",
    "Write a file numbers.txt with the numbers 1 to 5, one per line, then read it and report the sum.",
    "Create greeting.txt with 'hello world', then edit it so it says 'hello chimera'. Report the final text.",
    "Create config.json containing {\"debug\": false}, then change debug to true and show the file.",
    "Make a file a.txt with 'one' and a file b.txt with 'two', then list the folder.",
    "Create poem.txt with two short lines of your choice, then read it back.",
    "Write a Python file hello.py that prints 'hi'. Do not run it; read it back and describe it.",
    "Create list.txt with three fruit names, one per line, then read it and tell me the second one.",
    "Check whether readme.txt exists by listing the folder; if it does not, create it with 'readme'.",
)


def wilson(successes: int, trials: int) -> tuple[float, float]:
    return proportions.wilson(successes, trials, 1.959963984540054)


class _Counting:
    """The gateway with `num_ctx` on every call and a hard budget on the number of calls."""

    def __init__(self, inner: Any, total: dict[str, int], budget: int | None) -> None:
        self._inner, self._total, self._budget, self.calls = inner, total, budget, 0

    def _count(self) -> None:
        if self._budget is not None and self._total["calls"] >= self._budget:
            raise _Budget
        self._total["calls"] += 1
        self.calls += 1

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self._count()
        kwargs.setdefault("num_ctx", NUM_CTX)
        return self._inner.complete(*args, **kwargs)

    def stream_complete(self, *args: Any, **kwargs: Any) -> Any:
        self._count()
        kwargs.setdefault("num_ctx", NUM_CTX)
        return self._inner.stream_complete(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Budget(RuntimeError):
    pass


def generate_one(run_dir: Path, task: str, total: dict[str, int], budget: int | None) -> dict[str, Any]:
    from chimera.config import Settings
    from chimera.core.agent import Agent, AgentConfig
    from chimera.providers.gateway import LLMGateway
    from chimera.tools.edit import EditFileTool
    from chimera.tools.files import ListDirTool, ReadFileTool, WriteFileTool
    from chimera.tools.registry import ToolRegistry

    home, workspace = run_dir / "home", run_dir / "workspace"
    workspace.mkdir(parents=True)
    settings = Settings(_env_file=None, CHIMERA_HOME=str(home), CHIMERA_WIRE_LOG=True, CHIMERA_CACHE=False)  # type: ignore[call-arg]  # pydantic aliases
    assert settings.wire_log and not settings.cache
    backend = _Counting(LLMGateway(settings=settings), total, budget)
    registry = ToolRegistry()
    for tool in (ReadFileTool(workspace), WriteFileTool(workspace), EditFileTool(workspace), ListDirTool(workspace)):
        registry.register(tool)
    agent = Agent(backend, registry, AgentConfig(
        model=MODEL, max_steps=MAX_STEPS, project_root=workspace, temperature=0.2, thinking=False,
        trace_path=home / "traces.jsonl",
    ))
    started = time.monotonic()
    error = ""
    try:
        result = agent.run(task)
        stopped = result.stopped_reason
    except _Budget:
        raise
    except Exception as exc:  # noqa: BLE001 — a protocol failure is recorded, never replaced (Amendment 1 §3)
        stopped, error = "error", f"{type(exc).__name__}: {exc}"
    meta = {"task": task, "stopped": stopped, "error": error, "calls": backend.calls,
            "seconds": round(time.monotonic() - started, 1)}
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def generate(out: Path, *, replicas: int, limit: int | None, budget: int | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    plan = [(task_index, replica) for replica in range(replicas) for task_index in range(len(TASKS))][:limit]
    total = {"calls": 0}
    for task_index, replica in plan:
        run_dir = out / f"t{task_index:02d}-r{replica:02d}"
        if run_dir.exists():
            continue  # resumable: a finished run is never regenerated
        try:
            meta = generate_one(run_dir, TASKS[task_index], total, budget)
        except _Budget:
            shutil.rmtree(run_dir, ignore_errors=True)
            print(f"stopped: budget of {budget} model calls reached", flush=True)
            break
        print(f"{run_dir.name}: {meta['stopped']} {meta['seconds']}s {meta['error']}", flush=True)
    print(f"model calls this invocation: {total['calls']}")


def _steps(trace: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [step for row in rows for step in row.get("steps", [])]


def mutate(steps: list[dict[str, Any]], fault: str, rng: random.Random) -> list[dict[str, Any]]:
    """One registered fault applied to a copy of ``steps`` (Amendment 1 §2)."""
    steps = [dict(step) for step in steps]
    if fault == "omission":
        del steps[rng.randrange(len(steps))]
    elif fault == "fabrication":
        made_up = {"role": "assistant", "content": f"fabricated {rng.random()}"}
        steps.insert(rng.randrange(len(steps) + 1), {
            "index": len(steps), "content": made_up["content"], "tools": [],
            "wire_id": uuid.UUID(int=rng.getrandbits(128), version=4).hex,
            "request_digest": digest([{"role": "user", "content": "fabricated request"}]),
            "response_digest": digest(made_up),
        })
    elif fault == "altered copy":
        target = steps[rng.randrange(len(steps))]
        target["response_digest"] = digest({"content": f"a different response {rng.random()}"})
    elif fault != "clean":
        raise ValueError(fault)
    return steps


def inject(runs: Path, work: Path) -> dict[str, Any]:
    rng = random.Random(SEED)
    counts = {fault: [0, 0] for fault in CLASSES}
    failures: list[str] = []
    false_positives: list[dict[str, Any]] = []
    for run_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
        wire, trace = run_dir / "home" / "wire.jsonl", run_dir / "home" / "traces.jsonl"
        if not (wire.exists() and trace.exists()) or not _steps(trace):
            failures.append(run_dir.name)
            continue
        steps = _steps(trace)
        for fault in CLASSES:
            copy = work / run_dir.name / fault.replace(" ", "_")
            copy.mkdir(parents=True, exist_ok=True)
            step_path = copy / "traces.jsonl"
            step_path.write_text(json.dumps({"steps": mutate(steps, fault, rng)}) + "\n", encoding="utf-8")
            audit = reconcile(wire, step_path)
            flagged = not audit["clean"]
            counts[fault][0] += flagged
            counts[fault][1] += 1
            if fault == "clean" and flagged:
                false_positives.append({"run": run_dir.name, **audit})
    return {"counts": counts, "protocol_failures": failures, "clean_false_positives": false_positives}


def _rate(hits: int, trials: int) -> str:
    if not trials:
        return "0/0"
    low, high = wilson(hits, trials)
    return f"{hits}/{trials} ({hits / trials:.1%}; 95% Wilson {low:.1%}–{high:.1%})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--replicas", type=int, default=REPLICAS)
    gen.add_argument("--limit", type=int, help="first N runs only (smoke)")
    gen.add_argument("--max-calls", type=int, help="stop before exceeding this many model calls (smoke)")
    inj = sub.add_parser("inject")
    inj.add_argument("--runs", type=Path, required=True)
    inj.add_argument("--json", type=Path, help="also write the full result here")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    if args.command == "generate":
        generate(args.out, replicas=args.replicas, limit=args.limit, budget=args.max_calls)
        return
    work = args.runs.parent / (args.runs.name + "-mutated")
    shutil.rmtree(work, ignore_errors=True)
    result = inject(args.runs, work)
    for fault in CLASSES:
        hits, trials = result["counts"][fault]
        label = "false positives" if fault == "clean" else "detected"
        print(f"{fault}: {_rate(hits, trials)} {label}")
    pooled = [sum(result["counts"][f][i] for f in CLASSES[1:]) for i in (0, 1)]
    print(f"pooled faults: {_rate(pooled[0], pooled[1])} detected")
    print(f"protocol failures (not replaced): {len(result['protocol_failures'])} {result['protocol_failures']}")
    for row in result["clean_false_positives"]:
        print(f"  clean FP {row['run']}: missing_steplog={len(row['missing_steplog'])} missing_wire={row['missing_wire']} "
              f"altered={len(row['altered'])} duplicates={row['duplicate_ids']}")
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
