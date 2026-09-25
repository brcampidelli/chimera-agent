"""H7: does a recap sentence in the system prompt help a model that gets its spec one shard per turn?

    python bench/sharded_recap/run.py --check                     # $0: prompts, grader preflight
    python bench/sharded_recap/run.py --pilot --out results/pilot.json
    python bench/sharded_recap/run.py --run --pilot-file results/pilot.json --out results/run.json
    python bench/sharded_recap/run.py --report results/run.json

See PREREGISTRATION.md, registered before any call. Protocol of arXiv 2505.06120 ("LLMs Get Lost in
Multi-Turn Conversation"): the same spec either in one turn (FULL, the positive control) or one
shard per user turn with the model's replies kept in the history (SHARDED). Plain gateway calls, no
tools, the model pinned to one OpenRouter provider. The final reply's code runs against hidden tests.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from grading import grade_blocks, grade_reply  # noqa: E402
from tasks import TASKS, Task  # noqa: E402

from chimera.core.agent import DEFAULT_SYSTEM_PROMPT  # noqa: E402

MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
PROVIDER = "DeepInfra"
#: The chat surface's temperature: `AgentConfig.temperature` defaults to 0.2.
TEMPERATURE = 0.2

#: Registered in PREREGISTRATION.md. The coordinator's draft, unchanged.
RECAP = (
    "When the conversation has run over several turns, start your final answer by listing, in one "
    "short list, every requirement the user has given in any turn; then give the answer that meets "
    "all of them."
)
#: PROTOCOL §6: equally long, irrelevant text in the same slot. A true statement about the project.
PLACEBO = (
    "Chimera is open-source software released under the Apache-2.0 licence; its source code, its "
    "issue tracker and its documentation are all kept together in one public repository that anyone "
    "may read, copy, change or build from."
)
#: Appended to the last user turn in every condition, so every conversation ends on a request for
#: the whole code and the grader reads a complete answer rather than a patch.
CLOSING = "That is everything. Please give me the complete, final version of the code."
#: Amendment 1: the surface line (plan §5.1, L3), last in every condition's system prompt. The
#: shipped prompt tells the model to use tools these calls do not carry; on the first launch it
#: narrated file edits it could not make and then returned empty replies on 3 of 9 sharded runs.
SURFACE = (
    "No tools are available in this conversation, so no file can be created, read or run: write any "
    "code in your reply, in a fenced code block."
)
#: Amendment 1: spent on the discarded first launch (US$ 0.011 measured, rounded up), counted
#: against the cap.
DISCARDED_SPEND = 0.02

#: DeepInfra's list price for this model, read from OpenRouter's public endpoint list on 2026-09-25
#: ($ per token). Cache reads are priced at the full input rate, which overstates cost slightly.
PRICE_IN, PRICE_OUT = 0.06e-6, 0.18e-6
#: Stop launching new work once pilot + main have spent this much (the hard cap is US$ 1.50).
SPEND_STOP = 1.40


def chat_system_prompt() -> str:
    """The system message a chat-surface `Agent` composes, with the per-install layers left out.

    `chimera chat` builds a plain `Agent` with the default prompt and then appends retrieved skills,
    the workspace's AGENTS.md, the owner's identity and (when the tool is granted) the todo sentence.
    Those layers are per install, per workspace or per message, so they are off here; what is left
    must be `DEFAULT_SYSTEM_PROMPT` byte for byte, and the assertion below says so.
    """
    from chimera.core.agent import Agent, AgentConfig
    from chimera.tools.registry import ToolRegistry

    config = AgentConfig(model=MODEL, inject_skill_context=False, prefix_nonce="", project_root=None,
                         instructions="")
    return Agent(None, ToolRegistry(), config).compose_system_prompt("x")  # type: ignore[arg-type]


SYSTEM_A = chat_system_prompt()
assert SYSTEM_A == DEFAULT_SYSTEM_PROMPT, "the chat build changed; re-register before running"
#: L0, then the situation-layer sentence under test (B) or its placebo (P), then the surface line
#: (Amendment 1), in the plan's layer order.
SYSTEMS = {
    "F": SYSTEM_A + "\n\n" + SURFACE,
    "A": SYSTEM_A + "\n\n" + SURFACE,
    "B": SYSTEM_A + "\n\n" + RECAP + "\n\n" + SURFACE,
    "P": SYSTEM_A + "\n\n" + PLACEBO + "\n\n" + SURFACE,
}
assert abs(len(PLACEBO) - len(RECAP)) <= 0.15 * len(RECAP), "placebo must be as long as the recap"


def user_turns(task: Task, cond: str) -> list[str]:
    if cond == "F":
        return ["\n\n".join(task.shards) + "\n\n" + CLOSING]
    return [*task.shards[:-1], task.shards[-1] + "\n\n" + CLOSING]


# --------------------------------------------------------------------------------------------------
# Calls


class Pinned:
    """The gateway, every call pinned to one OpenRouter provider with no fallbacks."""

    def __init__(self) -> None:
        from chimera.providers import LLMGateway

        self.gateway = LLMGateway()

    def call(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """One reply. Up to three attempts on an exception or an empty reply; then a halt."""
        err = ""
        spent = 0.0
        log: list[str] = []  # Amendment 1: why each attempt ended, so an empty reply has a cause on record
        for attempt in range(3):
            t0 = time.time()
            try:
                r = self.gateway.complete(
                    messages, model=MODEL, temperature=TEMPERATURE,
                    extra_body={"provider": {"order": [PROVIDER], "allow_fallbacks": False}},
                )
            except Exception as exc:  # noqa: BLE001 — a provider failure is a halt, not a fail
                err = f"{type(exc).__name__}: {exc}"[:300]
                log.append(err)
                time.sleep(5 * (attempt + 1))
                continue
            usd = (r.prompt_tokens or 0) * PRICE_IN + (r.completion_tokens or 0) * PRICE_OUT
            spent += usd
            log.append(f"finish={r.finish_reason or '?'} tool_calls={len(r.tool_calls or [])} "
                       f"chars={len(r.content or '')} out={r.completion_tokens}")
            rec = {
                "content": r.content, "prompt_tokens": r.prompt_tokens or 0,
                "completion_tokens": r.completion_tokens or 0, "cache_read_tokens": r.cache_read_tokens,
                "provider": r.provider, "model": r.model, "finish": r.finish_reason,
                "seconds": round(time.time() - t0, 1), "usd": spent, "attempts": attempt + 1, "log": log,
            }
            if r.truncated:
                return {**rec, "halt": "truncated"}
            if r.provider and r.provider != PROVIDER:
                return {**rec, "halt": f"route {r.provider}"}
            if not (r.content or "").strip():
                err = "empty reply"
                continue
            return {**rec, "halt": None}
        return {"halt": err or "failed", "usd": spent, "content": "", "log": log}


def _turn_summary(got: dict[str, Any]) -> dict[str, Any]:
    from grading import code_blocks, recap_items

    content = got.get("content") or ""
    return {
        "halt": got.get("halt"), "usd": round(got.get("usd", 0.0), 7),
        "prompt_tokens": got.get("prompt_tokens"), "completion_tokens": got.get("completion_tokens"),
        "cache_read_tokens": got.get("cache_read_tokens"), "provider": got.get("provider"),
        "seconds": got.get("seconds"), "attempts": got.get("attempts"), "finish": got.get("finish"),
        "log": got.get("log"), "chars": len(content),
        "n_blocks": len(code_blocks(content)), "recap_items": recap_items(content), "head": content[:500],
    }


def converse(pinned: Pinned, task: Task, cond: str) -> dict[str, Any]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEMS[cond]}]
    turns: list[dict[str, Any]] = []
    usd = 0.0
    t0 = time.time()
    got: dict[str, Any] = {}
    for text in user_turns(task, cond):
        messages.append({"role": "user", "content": text})
        got = pinned.call(messages)
        usd += got.get("usd", 0.0)
        turns.append(_turn_summary(got))
        if got.get("halt"):
            return {"halt": got["halt"], "usd": usd, "turns": turns, "seconds": round(time.time() - t0, 1)}
        messages.append({"role": "assistant", "content": got["content"]})
    final = got["content"]
    return {"halt": None, "usd": usd, "turns": turns, "seconds": round(time.time() - t0, 1),
            "final": final, "graded": grade_reply(task, final)}


def run_unit(pinned: Pinned, task: Task, replica: int, conds: tuple[str, ...]) -> dict[str, Any]:
    """One (task, replica): its conditions start together, so they share the same minutes and route."""
    with ThreadPoolExecutor(max_workers=len(conds)) as pool:
        futures = {cond: pool.submit(converse, pinned, task, cond) for cond in conds}
        return {"task": task.id, "replica": replica, "runs": {c: futures[c].result() for c in conds}}


def execute(units: list[tuple[Task, int]], conds: tuple[str, ...], out: Path, spent_before: float,
            workers: int, label: str) -> dict[str, Any]:
    pinned = Pinned()
    partial = out.with_suffix(".partial.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text("", encoding="utf-8")
    rows: list[dict[str, Any]] = []
    usd = 0.0
    convs = {c: 0 for c in conds}
    halts = {c: 0 for c in conds}
    stop_reason = ""
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_unit, pinned, task, rep, conds): (task, rep) for task, rep in units}
        for fut in as_completed(futures):
            if fut.cancelled():
                continue
            try:
                row = fut.result()
            except Exception as exc:  # noqa: BLE001 — a harness error loses the unit, never the run
                task, rep = futures[fut]
                print(f"  HARNESS ERROR on {task.id} r{rep}: {type(exc).__name__}: {exc}"[:300], flush=True)
                continue
            rows.append(row)
            with partial.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            marks = []
            for c in conds:
                got = row["runs"][c]
                usd += got["usd"]
                convs[c] += 1
                halts[c] += int(got["halt"] is not None)
                marks.append(f"{c}=" + ("H" if got["halt"] else ("P" if got["graded"]["passed"] else ".")))
            print(f"  [{len(rows):>3}/{len(units)}] {row['task']:<22} r{row['replica']} {' '.join(marks)}  "
                  f"US${spent_before + usd:.3f}  {time.time() - t0:.0f}s", flush=True)
            if not stop_reason:
                if spent_before + usd >= SPEND_STOP:
                    stop_reason = f"spend stop: US${spent_before + usd:.3f} >= {SPEND_STOP}"
                for c in conds:
                    if convs[c] >= 10 and halts[c] / convs[c] > 0.10:
                        stop_reason = f"halt stop: condition {c} halted {halts[c]}/{convs[c]}"
                if stop_reason:
                    print(f"STOP RULE: {stop_reason}", flush=True)
                    for pending in futures:
                        pending.cancel()
    order = {(t.id, r): i for i, (t, r) in enumerate(units)}
    rows.sort(key=lambda row: order[(row["task"], row["replica"])])
    payload = {"label": label, "model": MODEL, "provider": PROVIDER, "temperature": TEMPERATURE,
               "conds": list(conds), "units_planned": len(units), "usd": usd, "spent_before": spent_before,
               "stop_reason": stop_reason, "halts": halts, "seconds": round(time.time() - t0),
               "systems": SYSTEMS, "recap": RECAP, "placebo": PLACEBO, "closing": CLOSING,
               "surface": SURFACE, "rows": rows}
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwrote {out}  US${usd:.4f} this run, US${spent_before + usd:.4f} in total; halts {halts}")
    return payload


# --------------------------------------------------------------------------------------------------
# Statistics


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (mid - half, mid + half)


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / 2**n)


def newcombe_paired(n11: int, n10: int, n01: int, n00: int) -> tuple[float, float, float]:
    """Difference p(second) - p(first) with Newcombe's method 10 interval. n10 = first only, n01 = second only."""
    n = n11 + n10 + n01 + n00
    p1, p2 = (n11 + n10) / n, (n11 + n01) / n
    l1, u1 = wilson(n11 + n10, n)
    l2, u2 = wilson(n11 + n01, n)
    den = (n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00)
    phi = (n11 * n00 - n10 * n01) / math.sqrt(den) if den > 0 else 0.0
    d = p2 - p1
    lo = d - math.sqrt(max(0.0, (p2 - l2) ** 2 - 2 * phi * (p2 - l2) * (u1 - p1) + (u1 - p1) ** 2))
    hi = d + math.sqrt(max(0.0, (u2 - p2) ** 2 - 2 * phi * (u2 - p2) * (p1 - l1) + (p1 - l1) ** 2))
    return d, lo, hi


def sign_flip(diffs: list[float], draws: int = 200_000) -> float:
    """Two-sided task-level randomisation test of sum(diffs) = 0 (exact when <= 20 tasks differ)."""
    nz = [d for d in diffs if d != 0]
    if not nz:
        return 1.0
    obs = abs(sum(nz)) - 1e-12
    if len(nz) <= 20:
        hits = 0
        for mask in range(2 ** len(nz)):
            s = sum(-d if (mask >> i) & 1 else d for i, d in enumerate(nz))
            hits += abs(s) >= obs
        return hits / 2 ** len(nz)
    rng = random.Random(7)
    hits = sum(abs(sum(d if rng.random() < 0.5 else -d for d in nz)) >= obs for _ in range(draws))
    return (hits + 1) / (draws + 1)


def _passed(run: dict[str, Any]) -> bool | None:
    return None if run["halt"] else bool(run["graded"]["passed"])


def paired(rows: list[dict[str, Any]], first: str, second: str) -> dict[str, Any]:
    """Pairs of (task, replica) where both conditions finished; the halted pair leaves (PROTOCOL §2)."""
    n11 = n10 = n01 = n00 = 0
    per_task: dict[str, list[int]] = {}
    for row in rows:
        if first not in row["runs"] or second not in row["runs"]:
            continue
        a, b = _passed(row["runs"][first]), _passed(row["runs"][second])
        if a is None or b is None:
            continue
        n11 += a and b
        n10 += a and not b
        n01 += b and not a
        n00 += not a and not b
        per_task.setdefault(row["task"], []).append(int(b) - int(a))
    n = n11 + n10 + n01 + n00
    if n == 0:
        return {"n": 0}
    d, lo, hi = newcombe_paired(n11, n10, n01, n00)
    diffs = [sum(v) / len(v) for v in per_task.values()]
    return {"n": n, "first": (n11 + n10) / n, "second": (n11 + n01) / n, "diff": d, "lo": lo, "hi": hi,
            "only_first": n10, "only_second": n01, "p_mcnemar": mcnemar_exact(n10, n01),
            "tasks": len(per_task), "tasks_up": sum(x > 0 for x in diffs),
            "tasks_down": sum(x < 0 for x in diffs), "p_task": sign_flip(diffs)}


def per_condition(rows: list[dict[str, Any]], cond: str) -> dict[str, Any]:
    runs = [row["runs"][cond] for row in rows if cond in row["runs"]]
    done = [r for r in runs if not r["halt"]]
    passed = [r for r in done if r["graded"]["passed"]]
    failed = [r for r in done if not r["graded"]["passed"] and not r["graded"]["no_code"]]
    last = 4
    earlier = [r for r in failed if any(s < last for s in r["graded"]["failed_shards"])]
    usd = sum(r["usd"] for r in runs)
    final_out = [r["turns"][-1]["completion_tokens"] or 0 for r in done]
    inter = [t for r in done for t in r["turns"][:-1]]
    return {
        "conversations": len(runs), "halts": len(runs) - len(done), "done": len(done),
        "passed": len(passed), "rate": len(passed) / len(done) if done else float("nan"),
        "ci": wilson(len(passed), len(done)), "no_code": sum(r["graded"]["no_code"] for r in done),
        "recap_present": sum(r["graded"]["recap_items"] >= 3 for r in done),
        "recap_items_mean": sum(r["graded"]["recap_items"] for r in done) / max(1, len(done)),
        "failed_with_code": len(failed), "failed_earlier_shard": len(earlier),
        "usd": usd, "usd_per_conv": usd / max(1, len(runs)),
        "usd_per_success": usd / len(passed) if passed else float("inf"),
        "final_completion_tokens_mean": sum(final_out) / max(1, len(final_out)),
        "intermediate_turns": len(inter), "intermediate_with_code": sum(t["n_blocks"] > 0 for t in inter),
        "seconds_mean": sum(r["seconds"] for r in done) / max(1, len(done)),
    }


def replica_floor(rows: list[dict[str, Any]], cond: str) -> dict[str, Any]:
    by_task: dict[str, list[bool]] = {}
    for row in rows:
        if cond in row["runs"]:
            p = _passed(row["runs"][cond])
            if p is not None:
                by_task.setdefault(row["task"], []).append(p)
    multi = {t: v for t, v in by_task.items() if len(v) >= 2}
    pairs = dis = 0
    for v in multi.values():
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                pairs += 1
                dis += v[i] != v[j]
    return {"tasks": len(multi), "mixed_tasks": sum(len(set(v)) > 1 for v in multi.values()),
            "pairwise_disagreement": dis / pairs if pairs else float("nan")}


def pilot_decision(pilot: dict[str, Any]) -> dict[str, Any]:
    """Registered: the gap FULL - SHARDED-A on the pilot, and the main run's size from its cost."""
    rows = pilot["rows"]
    gap = paired(rows, "A", "F")  # diff = F - A
    runs_f = [row["runs"]["F"] for row in rows]
    runs_a = [row["runs"]["A"] for row in rows]
    c_f = sum(r["usd"] for r in runs_f) / max(1, len(runs_f))
    c_a = sum(r["usd"] for r in runs_a) / max(1, len(runs_a))
    spent = pilot["usd"] + pilot.get("spent_before", 0.0)
    budget = 0.9 * (SPEND_STOP - spent)
    decision: dict[str, Any] = {"gap": gap, "usd_per_F": c_f, "usd_per_A": c_a, "spent": spent,
                                "budget_for_main": budget, "go": False, "k": 0, "placebo": False}
    if gap.get("n", 0) == 0 or gap["diff"] < 0.10:
        decision["reason"] = "gap under 10 pp: the recap has nothing to recover; uninformative, stop"
        return decision
    # The placebo outranks replicas 4 and 5: at this project's ICC (~0.7, PROTOCOL §8) a task's
    # fourth and fifth replica add about 0.04 of an observation between them, and PROTOCOL §6 asks
    # for the placebo outright.
    for k, with_p in ((5, True), (4, True), (3, True), (5, False), (4, False), (3, False)):
        cost = len(TASKS) * k * (c_f + (3 if with_p else 2) * c_a)
        if cost <= budget:
            decision.update(go=True, k=k, placebo=with_p, projected=cost,
                            reason=f"k={k}, placebo {'on' if with_p else 'off'}, projected US${cost:.3f}")
            return decision
    decision["reason"] = "even k=3 without the placebo does not fit the budget; stop"
    return decision


# --------------------------------------------------------------------------------------------------
# Modes


def check() -> None:
    print("=== system prompts (tail) ===")
    for cond in ("A", "B", "P"):
        s = SYSTEMS[cond]
        print(f"[{cond}] {len(s)} chars ... {s[-330:]!r}\n")
    print(f"recap {len(RECAP)} chars / {len(RECAP.split())} words; placebo {len(PLACEBO)} chars / "
          f"{len(PLACEBO.split())} words")
    t = TASKS[0]
    print("\n=== example, task", t.id, "===")
    print("FULL:", repr(user_turns(t, "F")[0]))
    for i, turn in enumerate(user_turns(t, "A")):
        print(f"SHARDED turn {i + 1}:", repr(turn))
    print("\n=== grader preflight: reference must pass all, naive (shard 0 only) must fail a later shard ===")
    bad = 0
    load_bearing_total = 0
    for task in TASKS:
        assert len(task.shards) == 5, task.id
        assert {s for s, _src in task.tests} >= {1, 2, 3, 4}, f"{task.id}: a later shard has no test"
        ref = grade_blocks(task, [task.reference])
        naive = grade_blocks(task, [task.naive])
        naive_later = [s for s in naive["failed_shards"] if s > 0]
        load_bearing_total += len(naive_later)
        ok = ref["passed"] and bool(naive_later)
        bad += not ok
        print(f"  {task.id:<24} ref {ref['n_passed']:>2}/{ref['n_tests']:<2} "
              f"naive {naive['n_passed']:>2}/{naive['n_tests']:<2} later shards it fails {naive_later}"
              f"{'' if ok else '   <-- DEFECT'}")
        if not ref["passed"]:
            for f in ref["failures"]:
                print("      ref failure:", f)
    print(f"\n{len(TASKS)} tasks, {sum(len(t.tests) for t in TASKS)} tests; later shards the naive "
          f"solution fails: {load_bearing_total}/{4 * len(TASKS)}; defects: {bad}")
    print("\n=== extraction on synthetic replies ===")
    t = TASKS[27]  # rotate
    samples = {
        "recap + code": "Requirements:\n- right rotation\n- negative k\n- big k\n\n```python\n" + t.reference + "\n```",
        "two blocks, usage last": "```python\n" + t.reference + "\n```\nUsage:\n```python\nprint(rotate([1, 2], 1))\n```",
        "no code": "Sure, noted. Anything else?",
        "patch only": "Change the return line to:\n```python\nreturn items[-k:] + items[:-k]\n```",
    }
    for name, reply in samples.items():
        g = grade_reply(t, reply)
        print(f"  {name:<24} passed={g['passed']} no_code={g['no_code']} recap_items={g['recap_items']} "
              f"blocks={g['n_blocks']}")
    if bad:
        raise SystemExit(f"{bad} task(s) failed the grader preflight")


def pilot(out: Path, workers: int) -> None:
    units = [(task, 0) for task in TASKS]
    payload = execute(units, ("F", "A"), out, DISCARDED_SPEND, workers, "pilot")
    print(json.dumps(pilot_decision(payload), indent=1, default=str))


def main_run(pilot_file: Path, out: Path, workers: int) -> None:
    pilot_payload = json.loads(pilot_file.read_text(encoding="utf-8"))
    decision = pilot_decision(pilot_payload)
    print(json.dumps(decision, indent=1, default=str))
    if not decision["go"]:
        raise SystemExit(f"pilot says stop: {decision['reason']}")
    conds = ("F", "A", "B", "P") if decision["placebo"] else ("F", "A", "B")
    units = [(task, rep) for rep in range(decision["k"]) for task in TASKS]
    execute(units, conds, out, decision["spent"], workers, "main")


def report(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["rows"]
    conds = payload["conds"]
    print(f"{payload['label']}: model {payload['model']} via {payload['provider']} T={payload['temperature']}  "
          f"units {len(rows)}/{payload['units_planned']}  US${payload['usd']:.4f} (total "
          f"US${payload['usd'] + payload.get('spent_before', 0):.4f})  stop: {payload['stop_reason'] or 'none'}")
    print("\npass rate per condition (final reply, all hidden tests):")
    stats = {}
    for c in conds:
        s = per_condition(rows, c)
        stats[c] = s
        lo, hi = s["ci"]
        print(f"  {c}: {s['passed']}/{s['done']} = {s['rate']:.3f} [{lo:.3f}, {hi:.3f}]  halts {s['halts']}  "
              f"no-code {s['no_code']}  recap>=3 items {s['recap_present']}/{s['done']} "
              f"(mean {s['recap_items_mean']:.1f})")
    print("\npaired comparisons (diff = second - first; McNemar exact; task-level sign-flip):")
    for first, second, label in (("A", "F", "positive control FULL - A"), ("A", "B", "PRIMARY B - A"),
                                 ("A", "P", "placebo P - A"), ("P", "B", "B - P")):
        if first in conds and second in conds:
            r = paired(rows, first, second)
            if r["n"]:
                print(f"  {label:<26} n={r['n']:>3}  {r['second']:.3f} vs {r['first']:.3f}  diff {r['diff']:+.3f} "
                      f"[{r['lo']:+.3f}, {r['hi']:+.3f}]  discordant {first}-only {r['only_first']} "
                      f"{second}-only {r['only_second']}  p={r['p_mcnemar']:.4g}  tasks up/down "
                      f"{r['tasks_up']}/{r['tasks_down']} of {r['tasks']}  p_task={r['p_task']:.4g}")
    print("\nfloors: replica disagreement within a condition")
    for c in conds:
        f = replica_floor(rows, c)
        print(f"  {c}: tasks with mixed outcomes {f['mixed_tasks']}/{f['tasks']}, pairwise disagreement "
              f"{f['pairwise_disagreement']:.3f}")
    print("\nfailures with code: how many failed a test of an EARLIER shard (lost requirement)")
    for c in conds:
        s = stats[c]
        print(f"  {c}: {s['failed_earlier_shard']}/{s['failed_with_code']}")
    print("\ncost and length")
    for c in conds:
        s = stats[c]
        print(f"  {c}: US${s['usd']:.4f}  per conversation US${s['usd_per_conv']:.5f}  per success "
              f"US${s['usd_per_success']:.5f}  final-turn completion tokens {s['final_completion_tokens_mean']:.0f}  "
              f"seconds/conv {s['seconds_mean']:.0f}  intermediate turns with code "
              f"{s['intermediate_with_code']}/{s['intermediate_turns']}")
    print("\nper task (passes per condition):")
    for task in TASKS:
        cells = []
        for c in conds:
            vals = [_passed(row["runs"][c]) for row in rows if row["task"] == task.id and c in row["runs"]]
            cells.append(f"{c} {sum(1 for v in vals if v)}/{sum(1 for v in vals if v is not None)}")
        print(f"  {task.id:<24} {'  '.join(cells)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--pilot-file", type=Path, default=HERE / "results" / "pilot.json")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    if args.check:
        check()
    elif args.pilot:
        pilot(args.out or HERE / "results" / "pilot.json", args.workers)
    elif args.run:
        main_run(args.pilot_file, args.out or HERE / "results" / "run.json", args.workers)
    elif args.report:
        report(args.report)
    else:
        ap.error("pass --check, --pilot, --run or --report")


if __name__ == "__main__":
    main()
