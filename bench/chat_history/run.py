"""Chat's history, flattened against real messages, on the sharded corpus, through `ChatSession`.

    python bench/chat_history/run.py --check                         # $0: prompts, grader preflight
    python bench/chat_history/run.py --run --out results/run.json    # paid; see PREREGISTRATION.md
    python bench/chat_history/run.py --report results/run.json

Each conversation is a real `ChatSession` over a real `Agent`, the way `chimera chat` and the
Discord bot build one, with the model pinned to one OpenRouter provider. The user reveals one shard
of a fully specified task per turn (arXiv 2505.06120); the final reply's code runs against hidden
tests. The corpus, the grader and the shard texts are H7's (`bench/sharded_recap`), unchanged.

Arms, interleaved per (task, replica):
- **FLAT**: `ChatSession(real_history=False)`, the shipped default.
- **REAL**: `ChatSession(real_history=True)`.
- **NOHIST** (positive control, replica 0 only): a fresh session given only the last turn.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
H7 = REPO / "bench" / "sharded_recap"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(H7))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from grading import code_blocks, grade_blocks, grade_reply  # noqa: E402
from tasks import TASKS, Task  # noqa: E402

from chimera.core.agent import DEFAULT_SYSTEM_PROMPT, Agent, AgentConfig  # noqa: E402
from chimera.interface.session import ChatSession  # noqa: E402
from chimera.skills.retrieval import SKILLS_HEADER  # noqa: E402
from chimera.tools.registry import ToolRegistry  # noqa: E402


def _load_h7() -> Any:
    """H7's runner, for its frozen texts and its statistics, loaded under a name of its own."""
    spec = importlib.util.spec_from_file_location("h7_run", H7 / "run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h7 = _load_h7()

MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
PROVIDER = "DeepInfra"
#: H7's texts, byte for byte: the surface line (its Amendment 1) and the closing line.
SURFACE: str = h7.SURFACE
CLOSING: str = h7.CLOSING
#: The chat agent's system message here: the shipped prompt, then H7's surface line, because these
#: calls carry no tools (H7 Amendment 1 measured the bare prompt on a tool-less surface failing).
SYSTEM = DEFAULT_SYSTEM_PROMPT + "\n\n" + SURFACE
#: DeepInfra's list price for this model, from H7 ($ per token). Cache reads are priced at the full
#: input rate, which overstates cost the same way in both arms.
PRICE_IN, PRICE_OUT = 0.06e-6, 0.18e-6
#: Registered: no new unit starts once this much has been spent, cache replay included.
SPEND_STOP = 1.30
REPLICAS = 4
ARMS = ("FLAT", "REAL")


class RouteError(RuntimeError):
    """A provider other than the pinned one answered."""


class _Pinned:
    """The gateway with every call pinned to one provider, logging each call of one conversation."""

    def __init__(self, gateway: Any, log: list[dict[str, Any]]) -> None:
        self.gateway = gateway
        self.log = log

    def complete(self, messages: list[Any], **kwargs: Any) -> Any:
        kwargs["extra_body"] = {"provider": {"order": [PROVIDER], "allow_fallbacks": False}}
        t0 = time.time()
        result = self.gateway.complete(messages, **kwargs)
        last = messages[-1] if isinstance(messages[-1], dict) else messages[-1].as_dict()
        self.log.append({
            "messages": len(messages),
            "prompt_tokens": result.prompt_tokens or 0,
            "cache_read_tokens": result.cache_read_tokens or 0,
            "completion_tokens": result.completion_tokens or 0,
            "provider": result.provider,
            "finish": result.finish_reason,
            "truncated": bool(result.truncated),
            "skills_in_turn": SKILLS_HEADER in str(last.get("content") or ""),
            "seconds": round(time.time() - t0, 1),
        })
        if result.provider and result.provider != PROVIDER:
            raise RouteError(f"answered by {result.provider}")
        return result


def user_turns(task: Task) -> list[str]:
    return [*task.shards[:-1], task.shards[-1] + "\n\n" + CLOSING]


def _session(gateway: Any, log: list[dict[str, Any]], *, real: bool) -> ChatSession:
    """A chat session as the chat surfaces build one, minus what varies per install.

    No tools (see ``SYSTEM``), no memory, no profile, no owner identity, no workspace. Skills stay
    on, as on every chat surface: under real history they are retrieved for the message, flattened
    for the whole block, and that difference is part of the change being measured.
    """
    agent = Agent(_Pinned(gateway, log), ToolRegistry(), AgentConfig(
        model=MODEL, system_prompt=SYSTEM, turn_context=True, project_root=None, instructions="",
    ))
    return ChatSession(agent, memory=None, graph=None, gate=None, real_history=real)


def converse(gateway: Any, task: Task, arm: str) -> dict[str, Any]:
    log: list[dict[str, Any]] = []
    t0 = time.time()
    turns = user_turns(task)
    if arm == "NOHIST":
        turns = turns[-1:]
    session = _session(gateway, log, real=(arm == "REAL"))
    replies: list[dict[str, Any]] = []
    try:
        for text in turns:
            answer = session.send(text)
            replies.append({"chars": len(answer), "n_blocks": len(code_blocks(answer)),
                            "empty": not answer.strip(), "head": answer[:400]})
    except Exception as exc:  # noqa: BLE001 — a provider failure is a halt, not a fail
        return {"halt": f"{type(exc).__name__}: {exc}"[:300], "calls": log, "replies": replies,
                "usd": _usd(log), "seconds": round(time.time() - t0, 1)}
    truncated = any(c["truncated"] for c in log)
    final = session.turns[-1].assistant
    return {"halt": "truncated" if truncated else None, "calls": log, "replies": replies,
            "usd": _usd(log), "seconds": round(time.time() - t0, 1), "final": final,
            "graded": grade_reply(task, final)}


def _usd(log: list[dict[str, Any]]) -> float:
    return sum(c["prompt_tokens"] * PRICE_IN + c["completion_tokens"] * PRICE_OUT for c in log)


def run_unit(gateway: Any, task: Task, replica: int, arms: tuple[str, ...]) -> dict[str, Any]:
    """One (task, replica): its arms start together, so they share the same minutes and route."""
    with ThreadPoolExecutor(max_workers=len(arms)) as pool:
        futures = {arm: pool.submit(converse, gateway, task, arm) for arm in arms}
        return {"task": task.id, "replica": replica, "runs": {a: futures[a].result() for a in arms}}


def execute(out: Path, workers: int, spent_before: float) -> dict[str, Any]:
    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    units: list[tuple[Task, int, tuple[str, ...]]] = []
    for rep in range(REPLICAS):
        for task in TASKS:
            arms = (*ARMS, "NOHIST") if rep == 0 else ARMS
            units.append((task, rep, arms))
    partial = out.with_suffix(".partial.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text("", encoding="utf-8")
    rows: list[dict[str, Any]] = []
    usd = 0.0
    convs = {a: 0 for a in (*ARMS, "NOHIST")}
    halts = {a: 0 for a in (*ARMS, "NOHIST")}
    stop_reason = ""
    lock = threading.Lock()
    t0 = time.time()
    # Replica 0 of every task is submitted before replica 1 of any, so a stop leaves whole replicas.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_unit, gateway, t, r, a): (t, r) for t, r, a in units}
        for fut in as_completed(futures):
            if fut.cancelled():
                continue
            try:
                row = fut.result()
            except Exception as exc:  # noqa: BLE001 — a harness error loses the unit, never the run
                task, rep = futures[fut]
                print(f"  HARNESS ERROR {task.id} r{rep}: {type(exc).__name__}: {exc}"[:300], flush=True)
                continue
            with lock:
                rows.append(row)
                with partial.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                marks = []
                for arm, got in row["runs"].items():
                    usd += got["usd"]
                    convs[arm] += 1
                    halts[arm] += int(got["halt"] is not None)
                    mark = "H" if got["halt"] else ("P" if got["graded"]["passed"] else ".")
                    marks.append(f"{arm}={mark}")
                print(f"  [{len(rows):>3}/{len(units)}] {row['task']:<22} r{row['replica']} "
                      f"{' '.join(marks)}  US${spent_before + usd:.3f}  {time.time() - t0:.0f}s",
                      flush=True)
                if not stop_reason:
                    if spent_before + usd >= SPEND_STOP:
                        stop_reason = f"spend stop: US${spent_before + usd:.3f} >= {SPEND_STOP}"
                    for arm in ARMS:
                        if convs[arm] >= 10 and halts[arm] / convs[arm] > 0.10:
                            stop_reason = f"halt stop: {arm} halted {halts[arm]}/{convs[arm]}"
                    if stop_reason:
                        print(f"STOP RULE: {stop_reason}", flush=True)
                        for pending in futures:
                            pending.cancel()
    order = {(t.id, r): i for i, (t, r, _a) in enumerate(units)}
    rows.sort(key=lambda row: order[(row["task"], row["replica"])])
    payload = {"model": MODEL, "provider": PROVIDER, "replicas": REPLICAS, "arms": [*ARMS, "NOHIST"],
               "units_planned": len(units), "usd": usd, "spent_before": spent_before,
               "stop_reason": stop_reason, "halts": halts, "seconds": round(time.time() - t0),
               "system": SYSTEM, "closing": CLOSING, "rows": rows}
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                   newline="\n")
    print(f"\nwrote {out}  US${usd:.4f} this run; halts {halts}")
    return payload


# --------------------------------------------------------------------------------------------------
# Statistics (the tests themselves are H7's: `newcombe_paired`, `mcnemar_exact`, `sign_flip`)


def _passed(run: dict[str, Any]) -> bool | None:
    return None if run["halt"] else bool(run["graded"]["passed"])


def per_task_differences(rows: list[dict[str, Any]], first: str, second: str) -> dict[str, float]:
    """Mean of (second - first) over each task's replicas where both arms finished."""
    by_task: dict[str, list[int]] = {}
    for row in rows:
        runs = row["runs"]
        if first in runs and second in runs:
            a, b = _passed(runs[first]), _passed(runs[second])
            if a is not None and b is not None:
                by_task.setdefault(row["task"], []).append(int(b) - int(a))
    return {t: sum(v) / len(v) for t, v in by_task.items()}


def cluster_bootstrap(diffs: list[float], draws: int = 20_000, seed: int = 25) -> tuple[float, float]:
    """Registered: 95% percentile interval of the mean per-task difference, resampling tasks."""
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(draws))
    return means[int(0.025 * draws)], means[int(0.975 * draws) - 1]


def arm_summary(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    runs = [row["runs"][arm] for row in rows if arm in row["runs"]]
    done = [r for r in runs if not r["halt"]]
    passed = [r for r in done if r["graded"]["passed"]]
    calls = [c for r in runs for c in r["calls"]]
    usd = sum(r["usd"] for r in runs)
    prompt = sum(c["prompt_tokens"] for c in calls)
    cached = sum(c["cache_read_tokens"] for c in calls)
    return {
        "conversations": len(runs), "halts": len(runs) - len(done), "passed": len(passed),
        "rate": len(passed) / len(done) if done else float("nan"),
        "ci": h7.wilson(len(passed), len(done)),
        "no_code": sum(r["graded"]["no_code"] for r in done),
        "empty_replies": sum(rep["empty"] for r in runs for rep in r["replies"]),
        "usd": usd, "usd_per_success": usd / len(passed) if passed else float("inf"),
        "prompt_tokens": prompt, "cached_tokens": cached,
        "cached_share": cached / prompt if prompt else float("nan"),
        "final_completion_tokens_mean": (
            sum(r["calls"][-1]["completion_tokens"] for r in done if r["calls"]) / max(1, len(done))
        ),
        "turns_with_skills": sum(c["skills_in_turn"] for c in calls), "calls": len(calls),
        "seconds_mean": sum(r["seconds"] for r in done) / max(1, len(done)),
    }


def decide(rows: list[dict[str, Any]], cache: dict[str, Any] | None) -> dict[str, Any]:
    """The registered decision rule, applied mechanically (PREREGISTRATION.md, "Decision rule")."""
    flat, real = arm_summary(rows, "FLAT"), arm_summary(rows, "REAL")
    nohist = arm_summary(rows, "NOHIST")
    flat_r0_rate = _replica0_rate(rows, "FLAT")
    real_r0_rate = _replica0_rate(rows, "REAL")
    control = nohist["rate"] <= flat_r0_rate - 0.30
    standing = real_r0_rate >= 0.75
    diffs = per_task_differences(rows, "FLAT", "REAL")
    lo, hi = cluster_bootstrap(list(diffs.values())) if diffs else (float("nan"), float("nan"))
    mean = sum(diffs.values()) / len(diffs) if diffs else float("nan")
    pair = h7.paired(_as_h7(rows), "FLAT", "REAL")
    non_inferior = lo >= -0.10
    harmful = hi < 0 and pair.get("p_mcnemar", 1.0) < 0.05
    guards = {
        "no_code": real["no_code"] / max(1, real["conversations"] - real["halts"])
        <= flat["no_code"] / max(1, flat["conversations"] - flat["halts"]) + 0.05,
        "cost_per_success": real["usd_per_success"] <= 1.2 * flat["usd_per_success"],
        "halts": all(s["halts"] <= 0.10 * max(1, s["conversations"]) for s in (flat, real)),
    }
    cache_improves = None
    if cache is not None:
        cache_improves = bool(cache["offline_real_not_reusable"] < cache["offline_flat_not_reusable"]
                              and cache["live_real_fewer_uncached_in_both_orders"])
    if not control:
        verdict = "UNINFORMATIVE: the positive control failed; success is not read"
    elif not standing:
        verdict = ("REAL PATH OR HARNESS BROKEN: REAL replica 0 below 75%; do not flip; read the "
                   "failures and say which in a dated amendment")
    elif harmful:
        verdict = "HARMFUL: real history loses success on this protocol; do not flip"
    elif not non_inferior:
        verdict = "NOT SHOWN NON-INFERIOR: do not recommend flipping"
    elif not all(guards.values()):
        verdict = f"GUARD FAILED {[k for k, v in guards.items() if not v]}: do not recommend flipping"
    elif cache_improves is False:
        verdict = "NON-INFERIOR BUT CACHE DID NOT IMPROVE: no reason to flip"
    elif cache_improves is None:
        verdict = "NON-INFERIOR; cache not yet read"
    else:
        verdict = "RECOMMEND FLIPPING: non-inferior, cache improves, guards hold"
    return {"flat": flat, "real": real, "nohist": nohist, "flat_r0_rate": flat_r0_rate,
            "real_r0_rate": real_r0_rate, "standing_check": standing,
            "control_holds": control, "tasks": len(diffs), "mean_task_diff": mean,
            "cluster_ci": (lo, hi), "pairs": pair, "p_task": h7.sign_flip(list(diffs.values())),
            "non_inferior": non_inferior, "harmful": harmful, "guards": guards,
            "cache_improves": cache_improves, "verdict": verdict}


def _replica0_rate(rows: list[dict[str, Any]], arm: str) -> float:
    runs = [row["runs"][arm] for row in rows if row["replica"] == 0 and arm in row["runs"]]
    done = [r for r in runs if not r["halt"]]
    return sum(1 for r in done if r["graded"]["passed"]) / len(done) if done else float("nan")


def _as_h7(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows in the shape H7's `paired` reads."""
    return [{"task": r["task"], "replica": r["replica"], "runs": r["runs"]} for r in rows]


def cache_inputs(prefix: Path, live: Path) -> dict[str, Any]:
    """The registered cache inputs: A1's non-reusable characters, A2's uncached tokens per order."""
    offline = json.loads(prefix.read_text(encoding="utf-8"))
    report = json.loads(live.read_text(encoding="utf-8"))
    both = all(
        o["runs"]["real"]["uncached_tokens_without_first"]
        < o["runs"]["flat"]["uncached_tokens_without_first"]
        for o in report["orders"]
    )
    pooled = report["pooled_without_first"]
    return {"offline_flat_not_reusable": offline["flat"]["chars_not_reusable"],
            "offline_real_not_reusable": offline["real"]["chars_not_reusable"],
            "offline_flat_share": offline["flat"]["reusable_share"],
            "offline_real_share": offline["real"]["reusable_share"],
            "live_real_fewer_uncached_in_both_orders": both,
            "live_flat_uncached": pooled["flat"]["uncached"],
            "live_real_uncached": pooled["real"]["uncached"],
            "live_flat_share": pooled["flat"]["share"], "live_real_share": pooled["real"]["share"],
            "live_usd": report["usd"]}


# --------------------------------------------------------------------------------------------------
# Modes


def check() -> None:
    print("=== system prompt (tail) ===")
    print(repr(SYSTEM[-400:]))
    t = TASKS[0]
    print("\n=== example turns,", t.id, "===")
    for i, turn in enumerate(user_turns(t)):
        print(f"turn {i + 1}:", repr(turn))
    print("\n=== grader preflight (H7's): reference passes, shard-0-only fails a later shard ===")
    bad = 0
    for task in TASKS:
        ref = grade_blocks(task, [task.reference])
        naive = grade_blocks(task, [task.naive])
        ok = ref["passed"] and any(s > 0 for s in naive["failed_shards"])
        bad += not ok
    print(f"{len(TASKS)} tasks, defects: {bad}")
    print("\n=== the two arms build what they claim, offline (scripted model, no network) ===")
    from chimera.providers.gateway import CompletionResult

    class _Echo:
        def __init__(self) -> None:
            self.sent: list[list[dict[str, Any]]] = []

        def complete(self, messages: list[Any], **kwargs: Any) -> Any:
            self.sent.append([dict(m) if isinstance(m, dict) else m.as_dict() for m in messages])
            return CompletionResult(content="```python\ndef f():\n    pass\n```", model="m")

    for arm in ARMS:
        echo = _Echo()
        agent = Agent(echo, ToolRegistry(), AgentConfig(  # type: ignore[arg-type]
            model=MODEL, system_prompt=SYSTEM, turn_context=True, project_root=None, instructions="",
        ))
        session = ChatSession(agent, memory=None, graph=None, gate=None, real_history=(arm == "REAL"))
        for text in user_turns(t):
            session.send(text)
        last = echo.sent[-1]
        print(f"{arm}: final request roles {[m['role'] for m in last]}; system == SYSTEM "
              f"{last[0]['content'] == SYSTEM}; last user message {len(last[-1]['content'])} chars")
    if bad:
        raise SystemExit(f"{bad} task(s) failed the grader preflight")


def report(path: Path, prefix: Path | None, live: Path | None) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["rows"]
    cache = cache_inputs(prefix, live) if prefix and live else None
    d = decide(rows, cache)
    print(f"model {payload['model']} via {payload['provider']}  units {len(rows)}/"
          f"{payload['units_planned']}  US${payload['usd']:.4f} (+{payload['spent_before']:.4f} "
          f"before)  stop: {payload['stop_reason'] or 'none'}\n")
    for arm in ("FLAT", "REAL", "NOHIST"):
        s = d[arm.lower()]
        lo, hi = s["ci"]
        print(f"{arm:>6}: {s['passed']}/{s['conversations'] - s['halts']} = {s['rate']:.3f} "
              f"[{lo:.3f}, {hi:.3f}]  halts {s['halts']}  no-code {s['no_code']}  empty replies "
              f"{s['empty_replies']}  US${s['usd']:.4f} (per success {s['usd_per_success']:.5f})  "
              f"prompt {s['prompt_tokens']} cached {s['cached_tokens']} ({s['cached_share']:.3f})  "
              f"final out {s['final_completion_tokens_mean']:.0f}  skills in {s['turns_with_skills']}"
              f"/{s['calls']} calls  {s['seconds_mean']:.0f}s/conv")
    p = d["pairs"]
    print(f"\npositive control: NOHIST {d['nohist']['rate']:.3f} vs FLAT r0 {d['flat_r0_rate']:.3f} "
          f"-> holds {d['control_holds']}")
    print(f"standing check: REAL r0 {d['real_r0_rate']:.3f} (H7 SHARDED-A 29/32 = 0.906; >= 0.75) "
          f"-> holds {d['standing_check']}")
    print(f"PRIMARY REAL - FLAT: task-level mean {d['mean_task_diff']:+.3f}, cluster bootstrap 95% "
          f"[{d['cluster_ci'][0]:+.3f}, {d['cluster_ci'][1]:+.3f}] over {d['tasks']} tasks; "
          f"sign-flip p={d['p_task']:.4g}")
    if p.get("n"):
        print(f"  pairs n={p['n']}: {p['second']:.3f} vs {p['first']:.3f}, diff {p['diff']:+.3f} "
              f"Newcombe [{p['lo']:+.3f}, {p['hi']:+.3f}], FLAT-only {p['only_first']} REAL-only "
              f"{p['only_second']}, McNemar p={p['p_mcnemar']:.4g}")
    print(f"non-inferior (lower >= -0.10): {d['non_inferior']}; harmful: {d['harmful']}; "
          f"guards {d['guards']}")
    for arm in ARMS:
        f = h7.replica_floor(_as_h7(rows), arm)
        print(f"replay floor {arm}: mixed tasks {f['mixed_tasks']}/{f['tasks']}, pairwise "
              f"disagreement {f['pairwise_disagreement']:.3f}")
    if cache:
        print(f"\ncache: {json.dumps(cache)}")
    print(f"\nVERDICT: {d['verdict']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--prefix", type=Path, help="measure_prefix.py --json output")
    ap.add_argument("--live", type=Path, help="replay_live.py output")
    ap.add_argument("--out", type=Path, default=HERE / "results" / "run.json")
    ap.add_argument("--spent-before", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    if args.check:
        check()
    elif args.run:
        execute(args.out, args.workers, args.spent_before)
    elif args.report:
        report(args.report, args.prefix, args.live)
    else:
        ap.error("pass --check, --run or --report")


if __name__ == "__main__":
    main()
