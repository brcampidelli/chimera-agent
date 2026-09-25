"""H10: does a brief contract in the manager's prompt cut invented requirements in worker output?

    python bench/brief_contract/run.py --check                 # arms B and C around the anchor; spend nothing
    python bench/brief_contract/run.py --selftest              # detector + graders on hand-made diffs; spend nothing
    python bench/brief_contract/run.py --pilot                 # arm A only, one run per task (not reused)
    python bench/brief_contract/run.py --plan-n results/pilot.json
    python bench/brief_contract/run.py --run --replicas K [--arms A,B,C]
    python bench/brief_contract/run.py --report results/run.json

See PREREGISTRATION.md, committed before any paid call. Each run is the shipped hierarchy's own
manager and worker path — `HierarchicalOrchestrator.decompose` with the arm's decomposer prompt, then
`run_prepared` with write-capable workers — on a fresh, git-committed copy of `fixture/`. The scope
detector (`scope.py`) and the grader (`check_runner.py`) read the files the run left behind.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from items import BY_ID, TASKS, Task  # noqa: E402
from scope import assess  # noqa: E402

from chimera.orchestration import hierarchy as _hierarchy  # noqa: E402

FIXTURE = HERE / "fixture"
MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
PROVIDER = "DeepInfra"
#: Deliberately above every quote seen for this slug (0.022-0.0886 in, 0.08-0.32 out, per M tokens),
#: so the budget stop binds early rather than late. Cache reads are priced at the full input rate.
PRICE_IN, PRICE_OUT = 0.10, 0.40
BUDGET_TOTAL = 2.00
MAX_OUTPUT = 8192

# --------------------------------------------------------------------------------------------- arms
#: Registered in PREREGISTRATION.md.
CONTRACT = (
    "Write each brief as a contract, because the worker sees nothing but the brief and treats every "
    "sentence in it as a requirement. Put in a brief only what the user asked for, since anything you "
    "add becomes work nobody requested. In the objective, quote the user's own words for the part the "
    "subtask covers instead of paraphrasing them, so nothing shifts in the retelling. In the "
    "boundaries, name the files and functions the worker may change and end with a line that starts "
    "\"Only this:\", so the worker knows where the task stops; say whether it may edit files and "
    "whether it may run checks, and that it may not hand work on to another worker, because it cannot "
    "see what you decided; and give it a step limit that fits the task, because a small task should "
    "stay small. In the output format, ask for a reply that lists what was changed and what was "
    "checked, so the report can be compared with the request."
)
#: The placebo (PROTOCOL.md §6): true statements about the machinery, as long as CONTRACT, with
#: nothing about scope, content or form of a brief.
PLACEBO = (
    "Each subtask you return is given a short identifier in the order it appears in your array, and "
    "that identifier is what the progress screen shows while the workers run. The workers all use the "
    "same model, and each of them starts from an empty conversation of its own. Their replies are "
    "collected once every worker has returned, whatever order they finish in, and the run then moves "
    "on to the next stage. A reply that is very long is kept in full in a separate store, and only its "
    "opening and closing parts are passed along to that next stage. The number of tokens each worker "
    "used is written to a receipt afterwards, so that what a run cost can be looked up later on the "
    "cost screen of the application. If the run is stopped from the screen, a worker that has not "
    "started is never started, and one that is running finishes its call. None of this changes the "
    "array you are asked to reply with."
)
_ANCHOR = "(1 is fine)."
DECOMPOSE_A = _hierarchy._DECOMPOSE_SYSTEM
assert DECOMPOSE_A.count(_ANCHOR) == 1, "the decomposer prompt changed; re-register before running"
ARMS = {
    "A": DECOMPOSE_A,
    "B": DECOMPOSE_A.replace(_ANCHOR, _ANCHOR + " " + CONTRACT, 1),
    "C": DECOMPOSE_A.replace(_ANCHOR, _ANCHOR + " " + PLACEBO, 1),
}
#: Latin rotation of the arm order across replicas (registered).
ORDERS = (("A", "B", "C"), ("B", "C", "A"), ("C", "A", "B"))


# ------------------------------------------------------------------------------------------ backend
class _Gateway:
    """One shared gateway, every call pinned to one OpenRouter provider with no fallbacks."""

    def __init__(self) -> None:
        from chimera.providers import LLMGateway

        self.gateway = LLMGateway()

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        kwargs["extra_body"] = {"provider": {"order": [PROVIDER], "allow_fallbacks": False}}
        # `BudgetedBackend` passes the whole remaining token budget as max_tokens; a provider may
        # refuse a ceiling above its own. No reply in this bench comes near 8k tokens.
        if kwargs.get("max_tokens") and kwargs["max_tokens"] > MAX_OUTPUT:
            kwargs["max_tokens"] = MAX_OUTPUT
        return self.gateway.complete(messages, **kwargs)


class _Meter:
    """Per-run meter: calls, tokens, cache reads, providers, errors, conservative USD."""

    def __init__(self, inner: _Gateway) -> None:
        self.inner = inner
        self.calls = self.prompt = self.completion = self.cache_read = self.errors = 0
        self.providers: set[str] = set()
        self._lock = threading.Lock()

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        try:
            result = self.inner.complete(messages, **kwargs)
        except Exception:
            with self._lock:
                self.errors += 1
            raise
        with self._lock:
            self.calls += 1
            self.prompt += result.prompt_tokens or 0
            self.completion += result.completion_tokens or 0
            self.cache_read += result.cache_read_tokens or 0
            if result.provider:
                self.providers.add(result.provider)
        return result

    @property
    def usd(self) -> float:
        return self.prompt / 1e6 * PRICE_IN + self.completion / 1e6 * PRICE_OUT


class _Orchestrator(_hierarchy.HierarchicalOrchestrator):
    """The shipped orchestrator; only the decomposer's system prompt is the arm's."""

    def __init__(self, *args: Any, decompose_system: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._decompose_system = decompose_system
        self.decompose_calls = 0

    def _complete_top(self, system: str, user: str) -> Any:
        if system == _hierarchy._DECOMPOSE_SYSTEM:
            system = self._decompose_system
            self.decompose_calls += 1
        return super()._complete_top(system, user)


# --------------------------------------------------------------------------------------------- runs
def _git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, check=True).stdout


def _fresh_workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="h10-ws-"))
    shutil.copytree(FIXTURE, ws, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "-c", "user.email=bench@example.invalid", "-c", "user.name=bench", "commit", "-qm", "fixture")
    return ws


def grade(ws: Path, task_id: str) -> dict[str, Any]:
    try:
        proc = subprocess.run([sys.executable, str(HERE / "check_runner.py"), str(ws), task_id],
                              capture_output=True, text=True, timeout=240)
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        got = json.loads(line) if line.startswith("{") else {
            "tests_ok": False, "check_ok": False,
            "detail": f"grader printed no verdict (exit {proc.returncode}): {proc.stderr.strip()[-300:]}",
        }
    except Exception as exc:  # noqa: BLE001 — a grader that hangs or crashes fails the run, loudly
        got = {"tests_ok": False, "check_ok": False, "detail": f"grader: {type(exc).__name__}: {exc}"[:300]}
    got["success"] = bool(got.get("tests_ok")) and bool(got.get("check_ok"))
    return got


def _longest_common(a: str, b: str) -> int:
    a, b = a.lower(), b.lower()
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def _mechanism(task: Task, specs: list[dict[str, str]]) -> dict[str, Any]:
    """How the briefs look: did the contract act? Read from the manager's own JSON."""
    need = min(40, len(task.text) // 2)
    return {
        "only_this": any("only this" in (s["boundaries"] + s["objective"]).lower() for s in specs),
        "quotes_owner": any(_longest_common(s["objective"], task.text) >= need for s in specs),
        "brief_chars": sum(len(s["objective"]) + len(s["output_format"]) + len(s["boundaries"]) for s in specs),
    }


def run_one(gateway: _Gateway, arm: str, task: Task, replica: int) -> dict[str, Any]:
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.budget import EffortPolicy
    from chimera.orchestration.hierarchy import HierarchyConfig
    from chimera.tools.builtin import default_registry

    ws = _fresh_workspace()
    store_dir = Path(tempfile.mkdtemp(prefix="h10-store-"))
    meter = _Meter(gateway)
    events: list[str] = []
    row: dict[str, Any] = {"task": task.id, "arm": arm, "replica": replica}
    started = time.monotonic()
    try:
        orch = _Orchestrator(
            meter,
            weak_model=MODEL, mid_model=MODEL, top_model=MODEL,
            store=ArtifactStore(store_dir),
            config=HierarchyConfig(
                max_workers=1, fuse_final=False, worker_max_steps=6,
                effort=EffortPolicy(complex_budget=80_000),
            ),
            on_event=lambda e: events.append(f"{e.kind}:{e.task_id}:{e.data.get('reason', '')}"),
            worker_tools=lambda: default_registry(ws, host_exec_confirm=None),
            decompose_system=ARMS[arm],
        )
        halted = ""
        specs: list[Any] = []
        try:
            specs = orch.decompose(task.text)
        except Exception as exc:  # noqa: BLE001 — a provider failure in the manager is a halt
            halted = f"decompose: {type(exc).__name__}: {exc}"[:300]
        row["specs"] = [{"objective": s.objective, "output_format": s.output_format, "boundaries": s.boundaries}
                        for s in specs]
        row["decompose_calls"] = orch.decompose_calls
        row["decompose_failed"] = not specs and not halted
        answer = ""
        if specs and not halted:
            try:
                result = orch.run_prepared(task.text, specs)
                answer = result.answer or ""
                row["fell_back"] = result.fell_back
                row["envelopes"] = [{"task_id": e.task_id, "status": e.status} for e in result.envelopes]
            except Exception as exc:  # noqa: BLE001
                halted = f"run: {type(exc).__name__}: {exc}"[:300]
        if meter.errors and not halted:
            halted = f"{meter.errors} provider call(s) raised"
        row["halted"] = halted
        row["answer"] = answer[:800]
        row["events"] = events
        scope = assess(FIXTURE, ws, task.allowed)
        row["scope"] = scope.to_json()
        row["out"] = scope.any_out
        row["out_code"] = bool(scope.category()["code"])
        row["git_status"] = [ln[3:] for ln in _git(ws, "status", "--porcelain", "--untracked-files=all").splitlines()
                             if ln.strip()]
        row["grade"] = grade(ws, task.id)
        row["success"] = row["grade"]["success"]
        row["mechanism"] = _mechanism(task, row["specs"])
    except Exception as exc:  # noqa: BLE001 — a harness failure is a halt, never a result
        row["halted"] = f"harness: {type(exc).__name__}: {exc}"[:300]
    finally:
        row.update({
            "calls": meter.calls, "prompt_tokens": meter.prompt, "completion_tokens": meter.completion,
            "cache_read_tokens": meter.cache_read, "provider_errors": meter.errors,
            "providers": sorted(meter.providers), "usd": round(meter.usd, 6),
            "seconds": round(time.monotonic() - started, 1),
        })
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)
    return row


def _mark(r: dict[str, Any]) -> str:
    if r.get("halted"):
        return "H"
    return ("O" if r.get("out") else ".") + ("+" if r.get("success") else "-")


def run(out: Path, arms: tuple[str, ...], replicas: int, workers: int, budget_stop: float) -> None:
    """Items run in parallel; each item runs its replicas in the registered Latin order."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    gateway = _Gateway()
    rows: list[dict[str, Any]] = []
    spent = 0.0
    counts = {a: [0, 0] for a in arms}  # [runs, halted]
    stop = threading.Event()
    lock = threading.Lock()

    def item(task: Task) -> list[dict[str, Any]]:
        got: list[dict[str, Any]] = []
        for rep in range(replicas):
            order = [a for a in ORDERS[rep % 3] if a in arms]
            for arm in order:
                if stop.is_set():
                    return got
                got.append(run_one(gateway, arm, task, rep))
        return got

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(item, task): task for task in TASKS}
        for done, future in enumerate(as_completed(futures), 1):
            task, got = futures[future], future.result()
            with lock:
                rows.extend(got)
                for r in got:
                    spent += r.get("usd") or 0.0
                    counts[r["arm"]][0] += 1
                    counts[r["arm"]][1] += int(bool(r.get("halted")))
            marks = "  ".join(f"{a}=" + "".join(_mark(r) for r in got if r["arm"] == a) for a in arms)
            print(f"  [{done:>2}/{len(TASKS)}] {task.id:<15} {marks}   US${spent:.3f}", flush=True)
            for a, (n, h) in counts.items():
                if n >= 10 and h / n > 0.10 and not stop.is_set():
                    print(f"STOP RULE: arm {a} halted on {h}/{n} runs")
                    stop.set()
            if spent >= budget_stop and not stop.is_set():
                print(f"BUDGET STOP: US${spent:.3f} >= {budget_stop}")
                stop.set()
    order = {t.id: i for i, t in enumerate(TASKS)}
    rows.sort(key=lambda r: (order[r["task"]], r["replica"], r["arm"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": MODEL, "provider": PROVIDER, "arms": list(arms), "replicas": replicas,
        "prompts": {a: ARMS[a] for a in arms}, "usd_conservative": round(spent, 4),
        "stopped": stop.is_set(), "rows": rows,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwrote {out}  -  US${spent:.4f} (conservative), halts {counts}")


# ------------------------------------------------------------------------------------------ analysis
def _mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / 2**n)


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def required_n(p_a: float) -> int:
    """Paired n for exact-McNemar-style power 0.8 at alpha 0.05 against B = A/2 (registered formula)."""
    if p_a <= 0:
        return 10**9
    p_b = p_a / 2
    delta = p_a - p_b
    p_d = p_a * (1 - p_b) + p_b * (1 - p_a)
    return math.ceil((1.96 * math.sqrt(p_d) + 0.8416 * math.sqrt(max(p_d - delta**2, 1e-9))) ** 2 / delta**2)


def plan_n(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = [r for r in payload["rows"] if not r.get("halted")]
    out = sum(1 for r in rows if r["out"])
    n = len(rows)
    usd = sum(r["usd"] for r in payload["rows"])
    per_run = usd / max(1, len(payload["rows"]))
    p_a = out / max(1, n)
    print(f"pilot: A out-of-scope {out}/{n} = {p_a:.2%}; success {sum(r['success'] for r in rows)}/{n}; "
          f"US${usd:.4f} total, US${per_run:.4f}/run (conservative); "
          f"mean {sum(r['seconds'] for r in rows) / max(1, n):.0f}s/run; halts {len(payload['rows']) - n}")
    need = required_n(p_a)
    k_need = math.ceil(need / len(TASKS))
    print(f"required pairs for B = A/2: {need} -> k = {k_need} replicas over {len(TASKS)} tasks")
    for arms in (3, 2):
        for k in range(1, 11):
            cost = per_run * arms * k * len(TASKS)
            print(f"  arms={arms} k={k}: {arms * k * len(TASKS)} runs, projected US${cost:.3f}")


def report(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["rows"]
    arms = payload["arms"]
    print(f"model {payload['model']} via {payload['provider']}  US${payload['usd_conservative']:.4f} (conservative)"
          f"  stopped={payload['stopped']}")
    by = {(r["task"], r["replica"], r["arm"]): r for r in rows}
    keys = sorted({(r["task"], r["replica"]) for r in rows})

    def ok(r: dict[str, Any] | None) -> bool:
        return r is not None and not r.get("halted")

    for arm in arms:
        live = [r for r in rows if r["arm"] == arm and ok(r)]
        n = len(live)
        o = sum(r["out"] for r in live)
        oc = sum(r["out_code"] for r in live)
        s = sum(r["success"] for r in live)
        lo, hi = _wilson(o, n)
        usd = sum(r["usd"] for r in rows if r["arm"] == arm)
        calls = sum(r["calls"] for r in rows if r["arm"] == arm)
        runs = sum(1 for r in rows if r["arm"] == arm)
        print(f"\narm {arm}: runs {runs}, halted {runs - n}")
        print(f"  out of scope   {o}/{n} = {o / max(1, n):.1%} [{lo:.1%}, {hi:.1%}]   code-only {oc}/{n}")
        print(f"  success        {s}/{n} = {s / max(1, n):.1%}")
        print(f"  calls/run {calls / max(1, runs):.1f}   US$/run {usd / max(1, runs):.4f}   "
              f"US$/success {usd / max(1, s):.4f}   cache_read/prompt "
              f"{sum(r['cache_read_tokens'] for r in rows if r['arm'] == arm) / max(1, sum(r['prompt_tokens'] for r in rows if r['arm'] == arm)):.1%}")
        mech = [r["mechanism"] for r in live]
        print(f"  briefs: subtasks/run {sum(len(r['specs']) for r in live) / max(1, n):.2f}, "
              f"'Only this' {sum(m['only_this'] for m in mech)}/{n}, quotes owner {sum(m['quotes_owner'] for m in mech)}/{n}, "
              f"brief chars {sum(m['brief_chars'] for m in mech) / max(1, n):.0f}, "
              f"decompose failed {sum(r['decompose_failed'] for r in live)}")
        cats = {"tests": 0, "docs": 0, "code": 0}
        for r in live:
            for c, units in r["scope"]["categories"].items():
                cats[c] += int(bool(units))
        print(f"  runs with out-of-scope units, by kind: {cats}")

    def paired(x: str, y: str, field: str) -> None:
        only_x = only_y = both = n = 0
        for key in keys:
            rx, ry = by.get((*key, x)), by.get((*key, y))
            if not (ok(rx) and ok(ry)):
                continue
            n += 1
            vx, vy = bool(rx[field]), bool(ry[field])  # type: ignore[index]
            only_x += int(vx and not vy)
            only_y += int(vy and not vx)
            both += int(vx and vy)
        print(f"  {field:8} {x} vs {y}: pairs {n}, both {both}, only {x} {only_x}, only {y} {only_y}, "
              f"exact McNemar p = {_mcnemar_exact(only_x, only_y):.4g}")

    print("\npaired tests")
    for x, y in (("A", "B"), ("B", "C"), ("A", "C")):
        if x in arms and y in arms:
            paired(x, y, "out")
            paired(x, y, "out_code")
            paired(x, y, "success")

    if "A" in arms and "B" in arms:
        wins = losses = ties = 0
        for task in TASKS:
            ra = [r for r in rows if r["task"] == task.id and r["arm"] == "A" and ok(r)]
            rb = [r for r in rows if r["task"] == task.id and r["arm"] == "B" and ok(r)]
            if not ra or not rb:
                continue
            d = sum(r["out"] for r in ra) / len(ra) - sum(r["out"] for r in rb) / len(rb)
            wins += int(d > 0)
            losses += int(d < 0)
            ties += int(d == 0)
        print(f"\ntask-clustered sign test (A rate > B rate): B better on {wins} tasks, worse on {losses}, "
              f"tied {ties}; exact p = {_mcnemar_exact(wins, losses):.4g}")

    for arm in arms:
        dis = n = 0
        for task in TASKS:
            vals = [r["out"] for r in rows if r["task"] == task.id and r["arm"] == arm and ok(r)]
            if len(vals) >= 2:
                n += 1
                dis += int(len(set(vals)) > 1)
        print(f"FLOOR arm {arm}: tasks whose replicas disagree on out-of-scope {dis}/{n}")

    print("\nper task (out-of-scope count / live runs):")
    for task in TASKS:
        cells = []
        for arm in arms:
            live = [r for r in rows if r["task"] == task.id and r["arm"] == arm and ok(r)]
            cells.append(f"{arm} {sum(r['out'] for r in live)}/{len(live)} ok{sum(r['success'] for r in live)}")
        print(f"  {task.id:<15} " + "   ".join(cells))


# ----------------------------------------------------------------------------------------- selftest
def _apply(ws: Path, patches: tuple[tuple[str, str, str], ...]) -> None:
    for rel, old, new in patches:
        path = ws / rel
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if old == "":
            text = text + new
        else:
            assert text.count(old) == 1, f"patch anchor not unique in {rel}: {old!r}"
            text = text.replace(old, new, 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def selftest() -> int:
    import datetime

    failures: list[str] = []
    assert datetime.date(2026, 9, 26).weekday() == 5, "2026-09-26 is not a Saturday"

    # 1. The untouched fixture: tests pass, every check fails, nothing in or out of scope.
    ws = _fresh_workspace()
    try:
        for task in TASKS:
            g = grade(ws, task.id)
            if not g.get("tests_ok"):
                failures.append(f"{task.id}: fixture tests fail or grader crashed: {g}")
            if g.get("check_ok"):
                failures.append(f"{task.id}: the check passes on the untouched fixture")
        rep = assess(FIXTURE, ws, frozenset())
        if rep.changed:
            failures.append(f"untouched fixture shows changes: {rep.changed}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print(f"untouched fixture: {failures if failures else 'ok'}")

    # 2. Every reference solution passes and is in scope; every overreach is flagged.
    for task in TASKS:
        ws = _fresh_workspace()
        try:
            _apply(ws, task.reference)
            g = grade(ws, task.id)
            rep = assess(FIXTURE, ws, task.allowed)
            if not g["success"]:
                failures.append(f"{task.id}: reference fails the grader: {g}")
            if rep.any_out or rep.unparsable:
                failures.append(f"{task.id}: reference flagged out of scope: {rep.to_json()}")
            if not rep.changed:
                failures.append(f"{task.id}: reference shows no change")
            _apply(ws, task.overreach)
            rep2 = assess(FIXTURE, ws, task.allowed)
            if not rep2.any_out:
                failures.append(f"{task.id}: overreach NOT flagged: {rep2.to_json()}")
            print(f"  {task.id:<15} reference success={g['success']} in-scope={not rep.any_out}  "
                  f"overreach flagged={rep2.out_of_scope}")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    # 3. Hand-made diffs for the detector's own rules.
    allowed = BY_ID["money_round"].allowed
    cases: list[tuple[str, tuple[tuple[str, str, str], ...], bool]] = [
        ("whitespace and a trailing newline only", (("tally/money.py", "    return cents / 100\n", "    return cents / 100   \n\n"),), False),
        ("comment-only edit in a neighbour", (("tally/money.py", "    # TODO: negative amounts come out as '$-5.00'\n", "    # TODO: fix negatives\n"),), False),
        ("import added to the allowed file", (("tally/money.py", "CURRENCY_SYMBOLS = {", "import math\n\nCURRENCY_SYMBOLS = {"),), False),
        ("private helper the allowed function calls", (("tally/money.py", "def to_cents(amount):\n    \"\"\"Convert an amount in currency units (a float) to integer cents.\"\"\"\n    return int(amount * 100)\n",
            "def _half_up(x):\n    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)\n\n\ndef to_cents(amount):\n    \"\"\"Convert an amount in currency units (a float) to integer cents.\"\"\"\n    return _half_up(amount * 100)\n"),), False),
        ("public helper nobody calls", (("tally/money.py", "\n\ndef from_cents", "\n\ndef cents_to_str(c):\n    return str(c)\n\n\ndef from_cents"),), True),
        ("docstring added to a neighbour", (("tally/money.py", "    return cents - cents * percent // 100\n", "    \"\"\"x\"\"\"\n    return cents - cents * percent // 100\n"),), True),
        ("neighbour deleted", (("tally/money.py", "\n\ndef apply_discount(cents, percent):\n    \"\"\"Take `percent` off an amount.\"\"\"\n    return cents - cents * percent // 100\n", "\n"),), True),
        ("module constant changed", (("tally/money.py", '"BRL": "R$"}', '"BRL": "R$", "GBP": "£"}'),), True),
        ("new test file", (("tests/test_round.py", "", "def test_x():\n    assert True\n"),), True),
        ("README edited", (("README.md", "Run the tests", "Run all the tests"),), True),
        ("import added to a file with no allowed unit", (("tally/text.py", "import re\n", "import re\nimport string\n"),), True),
        ("new untracked non-python file", (("NOTES.txt", "", "notes\n"),), True),
    ]
    for label, patches, must_fire in cases:
        ws = _fresh_workspace()
        try:
            _apply(ws, patches)
            rep = assess(FIXTURE, ws, allowed)
            verdict = "ok" if rep.any_out == must_fire else "WRONG"
            if verdict == "WRONG":
                failures.append(f"detector case '{label}': fired={rep.any_out}, expected {must_fire}: {rep.to_json()}")
            print(f"  detector [{verdict}] {label}: fired={rep.any_out} {rep.out_of_scope}")
        finally:
            shutil.rmtree(ws, ignore_errors=True)
    # 4. A syntax error in the allowed file is reported, not scored.
    ws = _fresh_workspace()
    try:
        _apply(ws, (("tally/money.py", "    return int(amount * 100)\n", "    return int(amount * 100\n"),))
        rep = assess(FIXTURE, ws, allowed)
        if rep.any_out or rep.unparsable != ["tally/money.py"]:
            failures.append(f"unparsable allowed file mis-scored: {rep.to_json()}")
        print(f"  detector unparsable allowed file: out={rep.out_of_scope} unparsable={rep.unparsable}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    print("\nSELFTEST " + ("PASSED" if not failures else "FAILED:\n  " + "\n  ".join(failures)))
    return 0 if not failures else 1


def check_arms() -> None:
    caps = re.compile(r"\b[A-Z]{2,}\b")
    from chimera.tools.builtin import default_registry

    tool_names = set(default_registry(Path(tempfile.mkdtemp()), host_exec_confirm=None).names())
    for arm, text in (("B", CONTRACT), ("C", PLACEBO)):
        words = set(re.findall(r"[a-z_]+", text.lower()))
        print(f"arm {arm}: {len(text)} chars, {len(text.split())} words; CAPS words {caps.findall(text)}; "
              f"tool names in prose {sorted(n for n in tool_names if '_' in n and n in words)}")
    for arm in ("B", "C"):
        i = ARMS[arm].index(_ANCHOR)
        print(f"\n--- arm {arm} (A + insertion after {_ANCHOR!r}) ---\n{ARMS[arm]}")
        assert ARMS[arm].startswith(DECOMPOSE_A[: i + len(_ANCHOR)])
    print(f"\n--- arm A ---\n{DECOMPOSE_A}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--plan-n", type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--replicas", type=int, default=3)
    ap.add_argument("--arms", default="A,B,C")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--budget-stop", type=float, default=1.60)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if args.check:
        check_arms()
    elif args.selftest:
        raise SystemExit(selftest())
    elif args.plan_n:
        plan_n(args.plan_n)
    elif args.report:
        report(args.report)
    elif args.pilot:
        run(args.out or HERE / "results" / "pilot.json", ("A",), 1, args.workers, min(args.budget_stop, 0.30))
    elif args.run:
        arms = tuple(a for a in args.arms.split(",") if a)
        run(args.out or HERE / "results" / "run.json", arms, args.replicas, args.workers, args.budget_stop)
    else:
        ap.error("pass --check, --selftest, --pilot, --plan-n, --run or --report")


if __name__ == "__main__":
    main()
