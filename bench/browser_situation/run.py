"""S11 harm check: the browser situation module off against on, on 24 browsing tasks. See PREREGISTRATION.md.

    python -m bench.browser_situation.run --run [--max-usd 0.95] [--workers 4]
    python -m bench.browser_situation.run --report

The site, its 24 tasks and their checkers are `bench/browser_viewport_tasks`'s, imported, not copied:
the pages are pinned by its manifest, so both arms browse the same bytes that step 3 browsed. Each solve
is its own process (its own local server, Chromium and CHIMERA_HOME) because the harness narrows the
SSRF check per process. Per task the order is OFF r0, ON r0, OFF r1, ON r1; tasks run in parallel.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
PROVIDER = "DeepInfra"
MAX_STEPS = 20
PER_SOLVE_USD = 0.05
REPLICAS = 2
ARMS = ("off", "on")


class _Pinned:
    """The gateway, every call pinned to one OpenRouter provider with no fallbacks."""

    def __init__(self) -> None:
        from chimera.providers import LLMGateway

        self.gateway = LLMGateway()

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        kwargs["extra_body"] = {"provider": {"order": [PROVIDER], "allow_fallbacks": False}}
        return self.gateway.complete(messages, **kwargs)


# --- one solve, in its own process ---------------------------------------------------------------


def _behaviour(calls: list[str], paths: list[str]) -> dict[str, int]:
    """The behaviours the module's rules name, counted from the action sequence and the server log."""
    changing = {"navigate", "click", "back", "type", "scroll"}
    both = reread = 0
    kinds: set[str] = set()
    prev = ""
    for action in calls:
        if action in changing:
            kinds = set()
        elif action in ("read", "read_text", "find"):
            kinds.add("list" if action == "read" else "text")
            both += int(len(kinds) > 1)
            reread += int(action == "read" and prev in changing)
        prev = action
    return {
        "second_look": both,
        "read_after_action": reread,
        "same_path_twice": sum(1 for a, b in zip(paths, paths[1:], strict=False) if a == b),
        "navigations": calls.count("navigate"),
    }


def solve(task_id: str, arm: str, replica: int, backend: Any = None) -> dict[str, Any]:
    """One solve. ``backend`` is None in the measurement (the pinned gateway); the dry-run passes a
    scripted stand-in so this exact path is exercised at US$ 0."""
    from bench.browser_viewport_tasks.harness import CallLog, CountedBrowser, only_this_origin
    from bench.browser_viewport_tasks.server import SiteServer
    from bench.browser_viewport_tasks.solve_one import cap_charge, ruler
    from bench.browser_viewport_tasks.tasks import BY_ID
    from chimera.core import Agent, AgentConfig
    from chimera.core.agent import partial_spend
    from chimera.tools import ToolRegistry
    from chimera.tools.browser import BrowserTool
    from chimera.tools.browser_playwright import PlaywrightDriver
    from chimera.tools.browser_situation import BrowserSituation

    task = BY_ID[task_id]
    record: dict[str, Any] = {"task": task_id, "arm": arm, "replica": replica, "error": "",
                              "ruler": ruler(), "provider_pin": PROVIDER}
    started = time.monotonic()
    server = SiteServer()
    try:
        with server as origin:
            driver = PlaywrightDriver(headless=True, allowed=lambda u: u == origin or u.startswith(origin + "/"))
            try:
                situation = BrowserSituation() if arm == "on" else None
                tool = BrowserTool(driver=driver, situation=situation)
                counted = CountedBrowser(tool, CallLog())
                registry = ToolRegistry()
                registry.register(counted)
                config = AgentConfig(model=MODEL, max_steps=MAX_STEPS, max_usd=PER_SOLVE_USD,
                                     browser_situation=(arm == "on"))
                agent = Agent(backend or _Pinned(), registry, config)
                with only_this_origin(origin):
                    try:
                        result = agent.run(task.prompt(origin))
                    except Exception as exc:  # noqa: BLE001 — the provider's failure, not the arm's
                        spend = partial_spend(exc)
                        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                        record["usd"] = spend.usd if spend else None
                        record["cap_usd"] = (
                            cap_charge(MODEL, spend.prompt_tokens, spend.completion_tokens, spend.usd)
                            if spend else PER_SOLVE_USD * 3
                        )
                        return record
                record["cap_usd"] = cap_charge(MODEL, result.prompt_tokens, result.completion_tokens, result.usd)
                calls = [str(c["action"]) for c in counted.log.calls]
                record.update({
                    "answer": (result.answer or "")[:1500],
                    "success": task.check.passes(result.answer or ""),
                    "stopped_reason": result.stopped_reason,
                    "handover": result.stopped_reason == "handover",
                    "steps": result.steps,
                    "tool_calls": result.tool_calls_made,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "cache_read_tokens": result.cache_read_tokens,
                    "usd": result.usd,
                    "providers": [str(getattr(s, "provider", "") or "") for s in result.steplog.steps],
                    "system_sha": result.steplog.system_sha,
                    "module_in_prompt": _module_in(result.transcript),
                    "browser_calls": calls,
                    "observation_chars": counted.log.chars,
                })
                if result.usd is None:
                    record["error"] = "unpriced: the receipt has no usd"
            finally:
                driver.close()
    except Exception as exc:  # noqa: BLE001 — Chromium or the server broke
        record["error"] = record["error"] or f"{type(exc).__name__}: {str(exc)[:300]}"
        record.setdefault("cap_usd", 0.0)
    finally:
        record["seconds"] = round(time.monotonic() - started, 1)
        record["requested_paths"] = server.requested[:200]
        record["behaviour"] = _behaviour(record.get("browser_calls", []), record["requested_paths"])
    return record


def _module_in(transcript: list[Any]) -> bool:
    """Whether the system message this solve sent carried the module — the arm, read off the run."""
    from chimera.tools.browser_situation import BROWSER_SITUATION_PROMPT

    system = next((m for m in transcript if isinstance(m, dict) and m.get("role") == "system"), {})
    return BROWSER_SITUATION_PROMPT in str(system.get("content", ""))


class _Scripted:
    """The dry-run's model: open the start page, then answer with the task's expected value."""

    def __init__(self, start: str, answer: str) -> None:
        self.start, self.answer, self.calls = start, answer, 0

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        from chimera.providers import CompletionResult, ToolCall

        self.calls += 1
        if self.calls == 1:
            call = ToolCall(id="1", name="browser", arguments={"action": "navigate", "url": self.start})
            # Token counts on every scripted reply: the per-solve ceiling refuses a reply it cannot
            # price, exactly as it would a real one.
            return CompletionResult(content="", model=MODEL, tool_calls=[call], prompt_tokens=10,
                                    completion_tokens=5)
        return CompletionResult(content=self.answer, model=MODEL, prompt_tokens=10, completion_tokens=5)


def dry_run() -> int:
    """US$ 0: both arms through the real site, Chromium and loop, with a scripted model. Checks that
    the arm reaches the prompt, that no ordinary page of the site hands over, and that the checker
    accepts the expected answer — before any paid call."""
    import re

    from bench.browser_viewport_tasks.tasks import TASKS

    ok = True
    for task in TASKS:
        for arm in ARMS:
            start = re.search(r"\((http://[^)]+)\)", task.prompt("http://ORIGIN"))
            backend = _Scripted("", task.check.expected)
            # The start page is only known once the server is up: patch it in on the first call.
            backend.start = start.group(1) if start else ""
            rec = _solve_with_origin(task.id, arm, backend)
            fine = (not rec.get("error") and rec.get("success") and not rec.get("handover")
                    and rec.get("module_in_prompt") == (arm == "on"))
            ok &= bool(fine)
            print(f"  {task.id:<3} {arm:<3} {'ok' if fine else 'XX'}  "
                  f"{rec.get('error') or rec.get('stopped_reason')} paths={rec.get('requested_paths', [])[:2]}")
    print("dry-run:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _solve_with_origin(task_id: str, arm: str, backend: _Scripted) -> dict[str, Any]:
    """`solve` with the scripted model's start address rewritten to the live server's origin."""
    import bench.browser_viewport_tasks.server as server_mod

    original = server_mod.SiteServer.__enter__

    def enter(self: Any) -> str:
        origin = original(self)
        backend.start = backend.start.replace("http://ORIGIN", origin)
        return origin

    server_mod.SiteServer.__enter__ = enter  # type: ignore[method-assign]
    try:
        return solve(task_id, arm, 0, backend=backend)
    finally:
        server_mod.SiteServer.__enter__ = original  # type: ignore[method-assign]


def solve_main(task: str, arm: str, replica: int, out: Path) -> int:
    # The code's defaults, not this machine's: every CHIMERA_* except credential pools goes, and the
    # arm comes only from the constructor and the AgentConfig above.
    for name in [n for n in os.environ if n.upper().startswith("CHIMERA_") and not n.upper().endswith("_KEYS")]:
        del os.environ[name]
    os.environ["CHIMERA_HOME"] = tempfile.mkdtemp(prefix="s11-home-")
    record = solve(task, arm, replica)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)
    return 1 if record.get("error") else 0


# --- the paid run --------------------------------------------------------------------------------


def _cell(task: str, arm: str, replica: int) -> Path:
    return RESULTS / "solves" / f"{task}__{arm}__r{replica}.json"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _attempt(task: str, arm: str, replica: int) -> dict[str, Any]:
    out = _cell(task, arm, replica)
    out.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "bench.browser_situation.run", "--solve", task, arm, str(replica),
             "--out", str(out)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
        )
        tail = (proc.stderr or "")[-300:]
    except subprocess.TimeoutExpired:
        tail = "timeout after 900 s"
    rec = _load(out) or {"task": task, "arm": arm, "replica": replica, "error": f"no record: {tail}"}
    if rec.get("error"):
        rec["stderr_tail"] = tail
    return rec


class _Budget:
    def __init__(self, cap: float, reserve: float) -> None:
        self.cap, self.reserve, self.spent, self.in_flight = cap, reserve, 0.0, 0
        self.lock = threading.Lock()
        self.halted = ""

    def take(self) -> bool:
        with self.lock:
            if self.halted or self.spent + (self.in_flight + 1) * self.reserve > self.cap:
                self.halted = self.halted or f"cap: spent US${self.spent:.4f} of US${self.cap}"
                return False
            self.in_flight += 1
            return True

    def settle(self, rec: dict[str, Any]) -> None:
        with self.lock:
            self.in_flight -= 1
            value = rec.get("cap_usd")
            self.spent += float(value) if value is not None else self.reserve


def _task_cells(task: str, budget: _Budget, log: Any, lock: threading.Lock) -> list[dict[str, Any]]:
    """One task's cells in the registered order, each resubmitted once on an apparatus error."""
    rows = []
    for replica in range(REPLICAS):
        for arm in ARMS:
            rec: dict[str, Any] = {}
            for _attempt_no in range(2):
                if not budget.take():
                    return rows
                rec = _attempt(task, arm, replica)
                budget.settle(rec)
                with lock:
                    log.write(json.dumps({k: rec.get(k) for k in (
                        "task", "arm", "replica", "success", "stopped_reason", "usd", "cap_usd", "error")}) + "\n")
                    log.flush()
                if not rec.get("error"):
                    break
            rows.append(rec)
            mark = "E" if rec.get("error") else ("H" if rec.get("handover") else ("✓" if rec.get("success") else "✗"))
            print(f"  {task:<3} {arm:<3} r{replica} {mark}  steps={rec.get('steps')} "
                  f"US${budget.spent:.4f}", flush=True)
    return rows


def paid_run(max_usd: float, workers: int) -> int:
    from bench.browser_viewport_tasks.solve_one import worst_price
    from bench.browser_viewport_tasks.tasks import TASKS

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("refusing: no OpenRouter key in the environment")
        return 2
    RESULTS.mkdir(parents=True, exist_ok=True)
    budget = _Budget(max_usd, PER_SOLVE_USD * worst_price(MODEL)[2])
    lock = threading.Lock()
    rows: list[dict[str, Any]] = []
    with (RESULTS / "driver.jsonl").open("a", encoding="utf-8") as log, ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(_task_cells, t.id, budget, log, lock) for t in TASKS]
        for fut in as_completed(futures):
            rows.extend(fut.result())
            errors = sum(1 for r in rows if r.get("error"))
            if len(rows) >= 10 and errors / len(rows) > 0.10 and not budget.halted:
                budget.halted = f"stop rule: {errors}/{len(rows)} solves errored"
    print(f"done: {len(rows)} solves, charged US${budget.spent:.4f}; {budget.halted or 'no stop'}")
    (RESULTS / "run_meta.json").write_text(json.dumps(
        {"solves": len(rows), "charged_usd": round(budget.spent, 6), "halted": budget.halted}, indent=1) + "\n",
        encoding="utf-8")
    return 0


# --- the report ----------------------------------------------------------------------------------


def _mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def report() -> dict[str, Any]:
    from bench.browser_viewport_tasks.tasks import TASKS

    recs = {}
    for path in sorted((RESULTS / "solves").glob("*.json")):
        rec = _load(path)
        if rec is not None and not rec.get("error"):
            recs[(rec["task"], rec["arm"], rec["replica"])] = rec
    pairs = [(t.id, r) for t in TASKS for r in range(REPLICAS)
             if (t.id, "off", r) in recs and (t.id, "on", r) in recs]
    b = sum(1 for t, r in pairs if recs[(t, "off", r)]["success"] and not recs[(t, "on", r)]["success"])
    c = sum(1 for t, r in pairs if not recs[(t, "off", r)]["success"] and recs[(t, "on", r)]["success"])
    lost_both = [t.id for t in TASKS if all(
        (t.id, a, r) in recs for a in ARMS for r in range(REPLICAS)) and all(
        recs[(t.id, "off", r)]["success"] and not recs[(t.id, "on", r)]["success"] for r in range(REPLICAS))]
    floor = sum(1 for t in TASKS if all((t.id, "off", r) in recs for r in range(REPLICAS))
                and recs[(t.id, "off", 0)]["success"] != recs[(t.id, "off", 1)]["success"])

    def arm_stats(arm: str) -> dict[str, Any]:
        rows = [recs[(t, arm, r)] for t, r in pairs]
        n = len(rows)
        succ = sum(1 for x in rows if x["success"])
        usd = sum(float(x.get("usd") or 0) for x in rows)
        behaviour = {k: sum(x["behaviour"][k] for x in rows) for k in rows[0]["behaviour"]} if rows else {}
        return {
            "solves": n, "success": succ, "handovers": sum(1 for x in rows if x.get("handover")),
            "mean_prompt_tokens": round(sum(x["prompt_tokens"] for x in rows) / max(n, 1)),
            "mean_uncached_prompt_tokens": round(
                sum(x["prompt_tokens"] - (x.get("cache_read_tokens") or 0) for x in rows) / max(n, 1)),
            "mean_tool_calls": round(sum(x["tool_calls"] for x in rows) / max(n, 1), 2),
            "mean_steps": round(sum(x["steps"] for x in rows) / max(n, 1), 2),
            "usd": round(usd, 6), "usd_per_success": round(usd / succ, 6) if succ else None,
            "behaviour": behaviour,
            "failed": sorted(f"{t}r{r}" for t, r in pairs if not recs[(t, arm, r)]["success"]),
            "providers": sorted({p for x in rows for p in x.get("providers", [])}),
        }

    off, on = arm_stats("off"), arm_stats("on")
    harm = (b - c) > 1 or bool(lost_both)
    out = {
        "pairs": len(pairs), "b_off_only": b, "c_on_only": c, "mcnemar_p": round(_mcnemar(b, c), 4),
        "lost_in_both_replicas": lost_both, "off_replica_flips": floor,
        "prompt_token_ratio_on_off": round(on["mean_prompt_tokens"] / off["mean_prompt_tokens"], 3)
        if off.get("mean_prompt_tokens") else None,
        "off": off, "on": on,
        "decision": "HARM: read the transcripts before anyone turns it on" if harm else "no harm detected",
    }
    (RESULTS / "summary.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--solve", nargs=3, metavar=("TASK", "ARM", "REPLICA"))
    ap.add_argument("--out")
    ap.add_argument("--max-usd", type=float, default=0.95)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    if args.solve:
        task, arm, replica = args.solve
        return solve_main(task, arm, int(replica), Path(args.out))
    if args.dry_run:
        return dry_run()
    if args.run:
        return paid_run(args.max_usd, args.workers)
    if args.report:
        report()
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
