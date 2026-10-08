"""S30-61, Amendment 3: LONG runs — does the steplog legitimately diverge from the wire log?

    uv run python bench/wire_reconcile/run_long.py generate --out bench/wire_reconcile/results/ollama-long
    uv run python bench/wire_reconcile/run_long.py generate --smoke --out <scratch>/smoke   # 4 runs (Amendment 4)
    uv run python bench/wire_reconcile/run_long.py check-smoke --runs <scratch>/smoke
    uv run python bench/wire_reconcile/run_long.py report --runs bench/wire_reconcile/results/ollama-long \
        --json bench/wire_reconcile/results/ollama-long.json

See PREREGISTRATION.md, Amendment 3. ``generate`` is the only step that calls a model: 100 runs of
8-15 calls on chained-file tasks, compaction forced on in every run, in four arms (S structural
compaction, M summarised compaction, F a forced mid-run switch to a fallback model, T streaming).
``report`` never calls one: it copies each run four times, mutates the copy's steplog only, reconciles
it against the untouched wire log, and attributes every discrepancy on a clean copy to the call that
produced it (``step``, ``summary`` or ``close``), using the per-call log ``generate`` wrote.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import random
import shutil
import statistics
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from chimera.eval import proportions
from chimera.governance.reconcile import digest, reconcile

MODEL = "ollama_chat/qwen3:4b"
FALLBACK = "ollama_chat/gemma4:12b"
NUM_CTX = 16384
MAX_STEPS = 15
REPLICAS = 10
SEED = 30613
#: Amendment 3 §3: the agent's own ContextBudget.threshold, in prompt tokens, and the kept tail.
THRESHOLD = 2500
KEEP_RECENT = 4
#: Amendment 3 §4, arm F: the primary is refused from this model call of the run onward.
SWITCH_AT = 4
#: Amendment 4: the smoke's closing-call run stops here; every chain needs at least 9 calls.
SMOKE_CLOSE_STEPS = 4
ARMS = ("S", "M", "F", "T")
ARM_NAMES = {"S": "structural compaction", "M": "summarised compaction", "F": "forced model switch",
             "T": "streaming"}
CLASSES = ("clean", "omission", "fabrication", "altered copy")
HOME_FILES = {"wire.jsonl", "traces.jsonl"}

#: (topic, chain length, final instruction) — Amendment 3 §2.
_FINALS = {
    "sum": "write total.txt containing only the sum of all the Value lines, read total.txt back, and tell me the sum.",
    "order": "write chain.md listing the file names in the order you read them, one per line, read chain.md "
             "back, and tell me how many files it lists.",
    "max": "write report.txt containing the largest Value, read report.txt back, and tell me which file held it.",
    "csv": "write values.csv with one Value per line in reading order, read values.csv back, and tell me how "
           "many lines it has.",
    "min": "write report.txt containing the smallest Value, read report.txt back, and tell me which file held it.",
    "sumedit": "write total.txt with the sum of the Values, then edit total.txt to add the word 'checked' on a "
               "second line, read total.txt back, and report what it says.",
}
TASKS = (
    ("orchard", 7, "sum"), ("harbor", 8, "order"), ("library", 6, "max"), ("observatory", 7, "csv"),
    ("foundry", 8, "sum"), ("archive", 6, "order"), ("garden", 7, "min"), ("railway", 8, "sumedit"),
    ("bakery", 6, "csv"), ("lighthouse", 7, "sum"),
)
_WORDS = ("amber", "birch", "cedar", "delta", "ember", "fjord", "grove", "heron", "iris", "juniper", "kestrel",
          "lumen", "maple", "nectar", "onyx", "pine", "quartz", "raven", "sable", "tundra", "umber", "willow")


def wilson(successes: int, trials: int) -> tuple[float, float]:
    return proportions.wilson(successes, trials, 1.959963984540054)


def task_text(task_index: int) -> str:
    topic, _, final = TASKS[task_index]
    return (
        f"This folder holds the {topic} ledger as a chain of files. Read start.txt first. Each file names "
        "the next one in its 'Next file:' line. Read the files one at a time, in that order, and do not open "
        "a file before the previous one names it; ignore any file the chain does not name. When a file says "
        f"'Next file: none', stop reading. Then {_FINALS[final]}"
    )


def chain(task_index: int) -> list[str]:
    """The chain's file names in reading order, deterministic per task (decoys are not in it)."""
    topic, length, _ = TASKS[task_index]
    words = random.Random(f"chain-{task_index}").sample(_WORDS, length + 2)
    return ["start.txt", *(f"{topic}-{word}.txt" for word in words[: length - 1])]


def seed_workspace(task_index: int, workspace: Path) -> None:
    topic, length, _ = TASKS[task_index]
    rng = random.Random(f"values-{task_index}")
    names = chain(task_index)
    for k, name in enumerate(names):
        nxt = names[k + 1] if k + 1 < len(names) else "none (this is the last record)"
        _write_record(workspace / name, topic, k + 1, rng.randint(10, 99), nxt)
    for word in random.Random(f"chain-{task_index}").sample(_WORDS, length + 2)[length - 1:]:
        _write_record(workspace / f"{topic}-{word}.txt", f"{topic} archive (decoy, not in the chain)", 0,
                      rng.randint(10, 99), "none (this file is not part of the ledger)")


def _write_record(path: Path, topic: str, k: int, value: int, nxt: str) -> None:
    filler = "".join(f"Line {j:02d}: entry {k}-{j} of the {topic} was counted, checked and filed without changes.\n"
                     for j in range(1, 17))
    path.write_text(f"Record {k} of the {topic}.\nValue: {value}\nNext file: {nxt}\n\n{filler}", encoding="utf-8")


def plan() -> list[tuple[int, int, str]]:
    """(task, replica, arm) in run order: replica-major, arms interleaved (Amendment 3 §4)."""
    return [(t, r, ARMS[(t + r) % 4]) for r in range(REPLICAS) for t in range(len(TASKS))]


def run_name(task_index: int, replica: int, arm: str) -> str:
    return f"r{replica:02d}-t{task_index:02d}-{arm}"


def smoke_plan() -> list[tuple[int, int, str, int]]:
    """Amendment 4 (§7 amended): (task, replica, arm, max_steps) for the 4 smoke runs.

    The first arm-F and arm-T runs of the plan (the new plumbing), the first arm-M run (to show the
    summary call), and the first arm-S run again at SMOKE_CLOSE_STEPS, which no chain can finish in,
    so it must end on a closing call. The 2-run smoke of Amendment 3 could show neither cause."""
    first = {arm: next(entry for entry in plan() if entry[2] == arm) for arm in ARMS}
    return [*((*first[arm], MAX_STEPS) for arm in ("F", "T", "M")), (*first["S"], SMOKE_CLOSE_STEPS)]


def entry_name(entry: tuple[Any, ...]) -> str:
    """A run's directory name; a run at a non-registered max_steps says so (``-max4``)."""
    task_index, replica, arm, *rest = entry
    max_steps = rest[0] if rest else MAX_STEPS
    name = run_name(task_index, replica, arm)
    return name if max_steps == MAX_STEPS else f"{name}-max{max_steps}"


class _Budget(RuntimeError):
    pass


class Outage:
    """Arm F: refuse the primary inside this process, before any request leaves it."""

    def __init__(self, model: str) -> None:
        self.model, self.active, self.refused = model, False, 0

    @contextlib.contextmanager
    def installed(self) -> Iterator[None]:
        import litellm
        from litellm.exceptions import ServiceUnavailableError

        original = litellm.completion

        def completion(**kwargs: Any) -> Any:
            if self.active and kwargs.get("model") == self.model:
                self.refused += 1
                raise ServiceUnavailableError(
                    message="injected outage (S30-61 Amendment 3, arm F)", llm_provider="ollama_chat",
                    model=self.model,
                )
            return original(**kwargs)

        litellm.completion = completion
        try:
            yield
        finally:
            litellm.completion = original


def _first_content(messages: Any) -> str:
    first = messages[0] if messages else None
    content = first.get("content") if isinstance(first, dict) else getattr(first, "content", None)
    return content if isinstance(content, str) else ""


class Recorder:
    """The backend the agent sees: the gateway, plus `num_ctx`, a call budget and a per-call log."""

    def __init__(self, inner: Any, total: dict[str, int], budget: int | None, outage: Outage | None) -> None:
        from chimera.core.summarise import SYSTEM

        self._inner, self._total, self._budget, self._outage = inner, total, budget, outage
        self._summary_system = SYSTEM
        self.calls: list[dict[str, str]] = []

    def _before(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        if self._budget is not None and self._total["calls"] >= self._budget:
            raise _Budget
        self._total["calls"] += 1
        if self._outage is not None and len(self.calls) + 1 >= SWITCH_AT:
            self._outage.active = True
        kwargs.setdefault("num_ctx", NUM_CTX)
        messages = args[0] if args else kwargs.get("messages")
        if kwargs.get("tools"):
            return "step"
        return "summary" if _first_content(messages) == self._summary_system else "close"

    def _after(self, kind: str, result: Any) -> Any:
        self.calls.append({"kind": kind, "wire_id": str(getattr(result, "wire_id", "") or ""),
                           "model": str(getattr(result, "model", "") or "")})
        return result

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        kind = self._before(args, kwargs)
        return self._after(kind, self._inner.complete(*args, **kwargs))

    def stream_complete(self, *args: Any, **kwargs: Any) -> Any:
        kind = self._before(args, kwargs)
        return self._after(kind, self._inner.stream_complete(*args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def compaction_fraction(model: str) -> float:
    """The `context_budget` fraction that puts this model's ContextBudget.threshold at THRESHOLD."""
    from chimera.core.context_budget import DEFAULT_TRIGGER, window_tokens

    budget = math.ceil(THRESHOLD / DEFAULT_TRIGGER)
    return (budget + 0.5) / window_tokens(model)


def generate_one(run_dir: Path, task_index: int, arm: str, total: dict[str, int], budget: int | None,
                 *, max_steps: int = MAX_STEPS) -> dict[str, Any]:
    from chimera.config import Settings
    from chimera.core.agent import Agent, AgentConfig
    from chimera.providers.gateway import LLMGateway
    from chimera.tools.edit import EditFileTool
    from chimera.tools.files import ListDirTool, ReadFileTool, WriteFileTool
    from chimera.tools.registry import ToolRegistry

    home, workspace = run_dir / "home", run_dir / "workspace"
    workspace.mkdir(parents=True)
    seed_workspace(task_index, workspace)
    fallback = [FALLBACK] if arm == "F" else []
    settings = Settings(_env_file=None, CHIMERA_HOME=str(home), CHIMERA_WIRE_LOG=True,  # type: ignore[call-arg]  # pydantic aliases
                        CHIMERA_CACHE=False, CHIMERA_FALLBACK_MODELS=fallback)
    assert settings.wire_log and not settings.cache and settings.fallback_models == fallback
    outage = Outage(MODEL) if arm == "F" else None
    backend = Recorder(LLMGateway(settings=settings), total, budget, outage)
    registry = ToolRegistry()
    for tool in (ReadFileTool(workspace), WriteFileTool(workspace), EditFileTool(workspace), ListDirTool(workspace)):
        registry.register(tool)
    fraction = compaction_fraction(MODEL)
    agent = Agent(backend, registry, AgentConfig(
        model=MODEL, max_steps=max_steps, project_root=workspace, temperature=0.2, thinking=False,
        trace_path=home / "traces.jsonl", context_budget=fraction, keep_recent=KEEP_RECENT,
        summarise_compaction=arm == "M",
    ))
    threshold = agent._budget.threshold if agent._budget is not None else 0
    assert threshold == THRESHOLD, f"compaction threshold {threshold}, registered {THRESHOLD}"
    started, error, stopped = time.monotonic(), "", ""
    try:
        with outage.installed() if outage is not None else contextlib.nullcontext():
            result = agent.run(task_text(task_index), on_token=(lambda _delta: None) if arm == "T" else None)
        stopped = result.stopped_reason
    except _Budget:
        raise
    except Exception as exc:  # noqa: BLE001 — a protocol failure is recorded, never replaced (Amendment 3 §8)
        stopped, error = "error", f"{type(exc).__name__}: {exc}"
    meta = _meta(home, task_index, arm, backend.calls, outage, stopped, error, threshold, fraction)
    meta["max_steps"] = max_steps
    meta["seconds"] = round(time.monotonic() - started, 1)
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _meta(home: Path, task_index: int, arm: str, calls: list[dict[str, str]], outage: Outage | None,
          stopped: str, error: str, threshold: int, fraction: float) -> dict[str, Any]:
    trace = home / "traces.jsonl"
    steps = _steps(trace) if trace.exists() else []
    models = [call["model"] for call in calls if call["model"]]
    kinds = Counter(call["kind"] for call in calls)
    return {
        "task_index": task_index, "task": task_text(task_index), "arm": arm, "stopped": stopped, "error": error,
        "n_calls": len(calls), "steps": len(steps), "step_calls": kinds["step"], "summaries": kinds["summary"],
        "closes": kinds["close"], "compactions": sum(bool(step.get("compacted")) for step in steps),
        "parallel_steps": sum(len(step.get("tools", [])) > 1 for step in steps),
        "refused_primary": outage.refused if outage is not None else 0,
        "switches": sum(a != b for a, b in zip(models, models[1:], strict=False)),
        "threshold": threshold, "fraction": fraction,
        "home_extra": sorted(p.name for p in home.iterdir() if p.name not in HOME_FILES) if home.exists() else [],
        "calls": calls,
    }


def generate(out: Path, entries: Sequence[tuple[Any, ...]], *, budget: int | None) -> None:
    """Run each (task, replica, arm[, max_steps]) entry; max_steps defaults to the registered one."""
    out.mkdir(parents=True, exist_ok=True)
    total = {"calls": 0}
    for entry in entries:
        task_index, _replica, arm, *rest = entry
        run_dir = out / entry_name(entry)
        if (run_dir / "meta.json").exists():
            continue  # resumable: a finished run is never regenerated
        shutil.rmtree(run_dir, ignore_errors=True)  # a run cut off mid-way left no meta: start it again
        try:
            meta = generate_one(run_dir, task_index, arm, total, budget, max_steps=rest[0] if rest else MAX_STEPS)
        except _Budget:
            shutil.rmtree(run_dir, ignore_errors=True)
            print(f"stopped: budget of {budget} model calls reached", flush=True)
            break
        print(f"{run_dir.name}: {meta['stopped']} calls={meta['n_calls']} steps={meta['steps']} "
              f"compactions={meta['compactions']} summaries={meta['summaries']} closes={meta['closes']} "
              f"switches={meta['switches']} {meta['seconds']}s {meta['error']}", flush=True)
    print(f"model calls this invocation: {total['calls']}")


def _steps(trace: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [step for row in rows for step in row.get("steps", [])]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def protocol_failure(run_dir: Path, meta: dict[str, Any]) -> str:
    """Why this run cannot be analysed (Amendment 3 §8), or ""."""
    wire, trace = run_dir / "home" / "wire.jsonl", run_dir / "home" / "traces.jsonl"
    if meta.get("error"):
        return str(meta["error"])
    if not trace.exists() or not _steps(trace):
        return "no steps"
    if not wire.exists() or not _jsonl(wire):
        return "no wire record"
    return ""


def check_smoke(runs: Path) -> tuple[bool, str]:
    """Amendment 3 §7 as amended by Amendment 4: proceed only if all 4 smoke runs exist, none is a
    protocol failure, and compaction fired in at least one. Whether each predicted false-positive
    cause showed (a summary call in the arm-M run, a closing call in the ``-max`` run) is printed
    for reading and does not decide."""
    expected = {entry_name(entry) for entry in smoke_plan()}
    metas = [(p, json.loads((p / "meta.json").read_text(encoding="utf-8")))
             for p in sorted(runs.iterdir()) if p.name in expected and (p / "meta.json").exists()]
    if len(metas) < len(expected):
        return False, f"expected {len(expected)} smoke runs, found {len(metas)}"
    failed = [f"{p.name}: {why}" for p, meta in metas if (why := protocol_failure(p, meta))]
    if failed:
        return False, "protocol failure in the smoke: " + "; ".join(failed)
    if not any(meta["compactions"] for _, meta in metas):
        return False, "compaction fired in no smoke run"
    summary = ", ".join(f"{p.name} compactions={m['compactions']} switches={m['switches']} calls={m['n_calls']} "
                        f"summaries={m.get('summaries', 0)} closes={m.get('closes', 0)} stopped={m.get('stopped', '')}"
                        for p, m in metas)
    summaries = sum(m.get("summaries", 0) for p, m in metas if p.name.endswith("-M"))
    closes = sum(m.get("closes", 0) for p, m in metas if "-max" in p.name)
    shown = (f"; causes shown (read only, decides nothing): summary calls in the arm-M run={summaries}, "
             f"closing calls in the max-steps run={closes}")
    return True, summary + shown


def mutate(steps: list[dict[str, Any]], fault: str, rng: random.Random) -> tuple[list[dict[str, Any]], str]:
    """Amendment 1's mutation, plus the wire_id its signature lives on (Amendment 3 §6)."""
    steps = [dict(step) for step in steps]
    if fault == "clean":
        return steps, ""
    if fault == "omission":
        return steps, str(steps.pop(rng.randrange(len(steps))).get("wire_id", ""))
    if fault == "fabrication":
        made_up = {"role": "assistant", "content": f"fabricated {rng.random()}"}
        position = rng.randrange(len(steps) + 1)  # drawn before the id, in Amendment 1's order
        wire_id = uuid.UUID(int=rng.getrandbits(128), version=4).hex
        steps.insert(position, {
            "index": len(steps), "content": made_up["content"], "tools": [], "wire_id": wire_id,
            "request_digest": digest([{"role": "user", "content": "fabricated request"}]),
            "response_digest": digest(made_up),
        })
        return steps, wire_id
    if fault == "altered copy":
        target = steps[rng.randrange(len(steps))]
        target["response_digest"] = digest({"content": f"a different response {rng.random()}"})
        return steps, str(target.get("wire_id", ""))
    raise ValueError(fault)


def signature_found(fault: str, audit: dict[str, Any], wire_id: str) -> bool:
    field = {"omission": "missing_steplog", "fabrication": "missing_wire", "altered copy": "altered"}[fault]
    return bool(wire_id) and wire_id in audit[field]


def causes(audit: dict[str, Any], kinds: dict[str, str]) -> Counter[str]:
    """Every discrepancy on a copy, attributed to what produced it."""
    found: Counter[str] = Counter()
    for wire_id in audit["missing_steplog"]:
        found[f"wire record without step ({kinds.get(wire_id, 'unknown call')})"] += 1
    for entry in audit["missing_wire"]:
        found["step without wire_id" if entry.startswith("step:") else "step with unknown wire_id"] += 1
    found["digest mismatch"] += len(audit["altered"])
    found["duplicate ids"] += audit["duplicate_ids"]
    return +found


def _order(path: Path) -> tuple[int, int]:
    replica, task = path.name.split("-")[:2]
    return int(replica[1:]), int(task[1:])


def analyse(runs: Path, work: Path) -> dict[str, Any]:
    rng = random.Random(SEED)
    signature = {fault: [0, 0] for fault in CLASSES[1:]}
    any_discrepancy = {fault: [0, 0] for fault in CLASSES}
    failures: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for run_dir in sorted((p for p in runs.iterdir() if (p / "meta.json").exists()), key=_order):
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        if why := protocol_failure(run_dir, meta):
            failures[run_dir.name] = why
            continue
        wire = run_dir / "home" / "wire.jsonl"
        steps = _steps(run_dir / "home" / "traces.jsonl")
        kinds = {call["wire_id"]: call["kind"] for call in meta["calls"] if call["wire_id"]}
        wire_models = {row["wire_id"]: row.get("model") for row in _jsonl(wire)}
        row: dict[str, Any] = {"run": run_dir.name, "arm": meta["arm"], "stopped": meta["stopped"],
                               "compactions": meta["compactions"], "summaries": meta["summaries"],
                               "closes": meta["closes"], "switches": meta["switches"], "n_calls": meta["n_calls"],
                               "parallel_steps": meta["parallel_steps"], "steps": len(steps),
                               "model_label_mismatch": sum(wire_models.get(s.get("wire_id"), s.get("model")) != s.get("model")
                                                           for s in steps),
                               "home_extra": meta["home_extra"]}
        for fault in CLASSES:
            copy = work / run_dir.name / fault.replace(" ", "_")
            copy.mkdir(parents=True, exist_ok=True)
            mutated, wire_id = mutate(steps, fault, rng)
            (copy / "traces.jsonl").write_text(json.dumps({"steps": mutated}) + "\n", encoding="utf-8")
            audit = reconcile(wire, copy / "traces.jsonl")
            any_discrepancy[fault][0] += not audit["clean"]
            any_discrepancy[fault][1] += 1
            if fault == "clean":
                row["clean_fp"] = not audit["clean"]
                row["causes"] = dict(causes(audit, kinds))
            else:
                signature[fault][0] += signature_found(fault, audit, wire_id)
                signature[fault][1] += 1
        rows.append(row)
    return {"runs": rows, "signature": signature, "any_discrepancy": any_discrepancy, "protocol_failures": failures}


def _rate(hits: int, trials: int) -> str:
    if not trials:
        return "0/0"
    low, high = wilson(hits, trials)
    return f"{hits}/{trials} ({hits / trials:.1%}; 95% Wilson {low:.1%}-{high:.1%})"


def verdict(result: dict[str, Any]) -> tuple[bool, list[str]]:
    """The registered rule (Amendment 3, 'Rule, fixed now'). Every reason it fails is listed."""
    rows = result["runs"]
    fp = sum(row["clean_fp"] for row in rows)
    reasons = []
    if len(rows) < 100:
        reasons.append(f"only {len(rows)} analysable runs (100 required)")
    if fp:
        reasons.append(f"{fp} clean false positives")
    for fault, (hits, trials) in result["signature"].items():
        if not trials or wilson(hits, trials)[0] < 0.90:
            reasons.append(f"{fault}: Wilson lower bound below 0.90 ({_rate(hits, trials)})")
    return not reasons, reasons


def report_lines(result: dict[str, Any]) -> list[str]:
    rows = result["runs"]
    lines = [f"analysable runs: {len(rows)}; protocol failures (not replaced): {len(result['protocol_failures'])} "
             f"{result['protocol_failures']}"]
    lines.append(f"PRIMARY clean false positives: {_rate(sum(r['clean_fp'] for r in rows), len(rows))}")
    for arm in ARMS:
        sub = [r for r in rows if r["arm"] == arm]
        lines.append(f"  arm {arm} ({ARM_NAMES[arm]}): {_rate(sum(r['clean_fp'] for r in sub), len(sub))} FP")
    subsets: tuple[tuple[str, Callable[[dict[str, Any]], bool]], ...] = (
                        ("compaction fired", lambda r: r["compactions"] > 0),
                        ("compaction NEVER fired (did not test compaction)", lambda r: r["compactions"] == 0),
                        ("arm F, switch happened", lambda r: r["arm"] == "F" and r["switches"] > 0),
                        ("arm F, NO switch (did not test the switch)", lambda r: r["arm"] == "F" and not r["switches"]),
                        ("arm M, summary call made", lambda r: r["arm"] == "M" and r["summaries"] > 0),
                        ("arm M, NO summary call", lambda r: r["arm"] == "M" and not r["summaries"]),
                        ("a closing call made", lambda r: r["closes"] > 0))
    for label, keep in subsets:
        sub = [r for r in rows if keep(r)]
        lines.append(f"  {label}: {len(sub)} runs, clean FP {_rate(sum(r['clean_fp'] for r in sub), len(sub))}")
    total: Counter[str] = Counter()
    affected: Counter[str] = Counter()
    for row in rows:
        total.update(row["causes"])
        affected.update(row["causes"].keys())
    lines.append("discrepancies on clean copies, by cause (count / runs affected):")
    lines += [f"  {cause}: {total[cause]} / {affected[cause]}" for cause in sorted(total)] or ["  none"]
    for fault, (hits, trials) in result["signature"].items():
        anyd = result["any_discrepancy"][fault]
        lines.append(f"{fault}: signature {_rate(hits, trials)} detected; any-discrepancy {_rate(*anyd)} (decides nothing)")
    calls = [r["n_calls"] for r in rows] or [0]
    lines.append(f"calls per run: median {statistics.median(calls)}, range {min(calls)}-{max(calls)}; "
                 f"compactions {sum(r['compactions'] for r in rows)}; switches {sum(r['switches'] for r in rows)}; "
                 f"stops {dict(Counter(r['stopped'] for r in rows))}; steps with >1 tool "
                 f"{sum(r['parallel_steps'] for r in rows)}/{sum(r['steps'] for r in rows)}; step/wire model "
                 f"label mismatches {sum(r['model_label_mismatch'] for r in rows)}; home/ extras "
                 f"{[r['run'] for r in rows if r['home_extra']]}")
    for row in rows:
        if row["clean_fp"]:
            lines.append(f"  clean FP {row['run']} arm={row['arm']} stopped={row['stopped']} causes={row['causes']}")
    supported, reasons = verdict(result)
    lines.append("RULE: supports an observe-only VPS pilot" if supported else f"RULE: no pilot — {'; '.join(reasons)}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--smoke", action="store_true", help="the four smoke runs only (Amendment 4, §7 amended)")
    gen.add_argument("--max-calls", type=int, help="stop before exceeding this many model calls")
    chk = sub.add_parser("check-smoke")
    chk.add_argument("--runs", type=Path, required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--runs", type=Path, required=True)
    rep.add_argument("--json", type=Path, help="also write the full result here")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    if args.command == "generate":
        generate(args.out, smoke_plan() if args.smoke else plan(), budget=args.max_calls)
        return
    if args.command == "check-smoke":
        ok, why = check_smoke(args.runs)
        print(("smoke OK: " if ok else "smoke FAILED: ") + why)
        raise SystemExit(0 if ok else 1)
    work = args.runs.parent / (args.runs.name + "-mutated")
    shutil.rmtree(work, ignore_errors=True)
    result = analyse(args.runs, work)
    for line in report_lines(result):
        print(line)
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
