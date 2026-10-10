"""Three-arm harness for S30-48 (empty commitments). See PREREGISTRATION.md and its Amendment 1.

    uv run python bench/empty_commitments/run_arms.py --model qwen3:4b \
        --requests bench/empty_commitments/requests.jsonl --out bench/empty_commitments/results/qwen3-4b.jsonl
    uv run python bench/empty_commitments/run_arms.py --report bench/empty_commitments/results/qwen3-4b.jsonl
    uv run python bench/empty_commitments/run_arms.py --blind-sheet bench/empty_commitments/results/qwen3-4b.jsonl

`run` takes any :class:`Backend`, so the tests drive it with a fake. :class:`ChatRuntimeBackend` is the
real one: the chat-platform bot surface (`chimera/server/manager.py`) rebuilt per turn against local
Ollama, with the arm's opt-in switched on and nothing else changed.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ARMS = ("A", "B", "C")
#: Amendment 1 §4: Ollama reads only half of num_ctx of a prompt and cuts the rest silently.
NUM_CTX = 16384
TRUNCATION_GUARD = 8000
#: Amendment 1 §7.
CALLS_PER_TURN = 8
MAX_STEPS = 6  # `chimera serve --max-steps` default
_ON = {"1", "true", "yes", "on"}


class Backend(Protocol):
    def reply(self, prompt: str, *, arm: str, tool_enabled: bool) -> tuple[str, list[dict[str, object]]]: ...


class CallCap(RuntimeError):
    """The turn reached its call ceiling (Amendment 1 §7) or the run reached its budget."""


def run(
    requests: list[dict[str, str]],
    backend: Backend,
    *,
    on_row: Callable[[dict[str, object]], None] | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for request in requests:
        for arm in ARMS:
            answer, events = backend.reply(
                request["request"], arm=arm, tool_enabled=arm == "C"
            )
            row: dict[str, object] = {"request_id": request["id"], "lang": request["lang"], "family": request["family"], "arm": arm, "answer": answer, "events": events}
            rows.append(row)
            if on_row is not None:
                on_row(row)
    return rows


def load_requests(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class _CountingBackend:
    """The gateway, with a call counter, a ceiling, and `num_ctx` on every call (Amendment 1 §4, §7)."""

    def __init__(self, inner: Any, *, budget: int | None) -> None:
        self._inner = inner
        self.calls = 0
        self.turn_calls = 0
        self._budget = budget

    def _count(self) -> None:
        if self.turn_calls >= CALLS_PER_TURN:
            raise CallCap("call_cap")
        if self._budget is not None and self.calls >= self._budget:
            raise CallCap("budget")
        self.calls += 1
        self.turn_calls += 1

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


@contextmanager
def _env(name: str, value: str | None) -> Iterator[None]:
    before = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = before


class ChatRuntimeBackend:
    """The platform bot's chat turn, rebuilt per request (Amendment 1 §1–§7)."""

    def __init__(self, model: str, root: Path, *, budget: int | None = None) -> None:
        from chimera.providers.gateway import LLMGateway

        self.model = model if "/" in model else f"ollama_chat/{model}"
        self.root = root
        self.backend = _CountingBackend(LLMGateway(), budget=budget)
        self._turn = 0

    def reply(self, prompt: str, *, arm: str, tool_enabled: bool) -> tuple[str, list[dict[str, object]]]:
        from chimera.cli.commands._shared import owner_identity
        from chimera.config import get_settings
        from chimera.core.agent import Agent, AgentConfig, attended
        from chimera.governance.profile import governed_profile
        from chimera.integrations.messaging import SenderRegistry, SendMessageTool
        from chimera.interface.session import ChatSession
        from chimera.server.gateway import InboundMessage, channel_note
        from chimera.tools.builtin import default_registry
        from chimera.tools.schedule_once import ScheduleOnceTool

        self._turn += 1
        turn_dir = self.root / f"turn-{self._turn:03d}-{arm}"
        workspace = turn_dir / "workspace"
        workspace.mkdir(parents=True)
        settings = get_settings()
        events: list[dict[str, object]] = []
        registry, _approvals = governed_profile(
            default_registry(workspace, host_exec_confirm=None),
            settings=settings, home=settings.home, surface="app-messaging:bench",
            voice=[SendMessageTool(SenderRegistry())],
        )
        if tool_enabled:
            def grant(action: str, reason: str) -> bool:
                events.append({"approval": "granted", "action": action, "reason": reason})
                return True

            registry.register(ScheduleOnceTool(home=turn_dir, workspace=workspace, approve=grant))
        trace = turn_dir / "trace.jsonl"
        agent = Agent(self.backend, registry, attended(AgentConfig(
            model=self.model, max_steps=MAX_STEPS, project_root=workspace,
            instructions=owner_identity(settings.home), turn_context=True,
            # `thinking=False` is a no-op on `ollama_chat/` (the gateway forwards it to OpenRouter
            # only): qwen3 reasons, as it does in the bot. Amendment 2 §1.
            temperature=0.0, thinking=False, trace_path=trace,
        )))
        session = ChatSession(agent)
        note_env = "1" if arm == "B" else None
        with _env("CHIMERA_CHAT_STATED_RUNTIME", note_env):
            note = channel_note(InboundMessage(text=prompt, chat_id="bench", platform="discord", user="owner"))
        self.backend.turn_calls = 0
        started = time.monotonic()
        stopped = "final"
        try:
            answer = session.send(prompt, channel_note=note)
        except CallCap as cap:
            if str(cap) == "budget":
                raise
            answer, stopped = "", "call_cap"
        events.extend(_tool_events(trace))
        events.append({
            "meta": {"utc": datetime.now(UTC).isoformat(), "calls": self.backend.turn_calls,
                     "seconds": round(time.monotonic() - started, 1), "stopped": stopped,
                     "max_prompt_tokens": _max_prompt(trace)},
        })
        if _max_prompt(trace) >= TRUNCATION_GUARD:
            raise SystemExit(f"turn {turn_dir.name}: a prompt reached {TRUNCATION_GUARD} tokens — possibly truncated")
        return answer, events


def _trace_steps(trace: Path) -> list[dict[str, Any]]:
    if not trace.exists():
        return []
    steps: list[dict[str, Any]] = []
    for line in trace.read_text(encoding="utf-8").splitlines():
        if line.strip():
            steps.extend(json.loads(line).get("steps", []))
    return steps


def _max_prompt(trace: Path) -> int:
    return max((int(step.get("prompt_tokens") or 0) for step in _trace_steps(trace)), default=0)


def _tool_events(trace: Path) -> list[dict[str, object]]:
    return [
        {"tool": tool.get("name"), "arguments": tool.get("arguments"), "observation": tool.get("observation"), "ok": tool.get("ok")}
        for step in _trace_steps(trace) for tool in step.get("tools", [])
    ]


def scheduled(events: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Each same-turn `schedule_once` call, with the outcome census.py reads (Amendment 1 §5)."""
    out: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        if event.get("tool") == "schedule_once":
            created = str(event.get("observation") or "").startswith("schedule created:")
            out.append(("schedule_once", {"ok": created, "status": "created" if created else "failed"}))
    return out


def _own_census() -> Any:
    """This bench's census.py, loaded by path.

    A bare `import census` returns whatever `census` is already in sys.modules — and two other
    benches (explorer_census, manager_advisory) ship a census.py too. In the full suite the explorer
    one is imported at collection, so `census.classify` did not exist and the pre-label raised."""
    import importlib.util

    name = "empty_commitments_census"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / "census.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def pre_label(row: dict[str, Any]) -> dict[str, int]:
    """The lexical census over one row — a convenience, NOT the registered blind label (Amendment 1 §8)."""
    census = _own_census()
    calls = [{"name": name, "result": result} for name, result in scheduled(list(row["events"]))]
    message = {"role": "assistant", "content": row["answer"], "tool_calls": calls}
    counts: dict[str, int] = census.classify([message])["counts"]
    return counts


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        mine = [row for row in rows if row["arm"] == arm]
        totals = {"empty": 0, "false_claim": 0, "unanchored": 0, "over_refusal": 0}
        for row in mine:
            for key, value in pre_label(row).items():
                totals[key] += value
        created = sum(any(r["ok"] for _, r in scheduled(list(row["events"]))) for row in mine)
        capped = sum(any(e.get("meta", {}).get("stopped") == "call_cap" for e in row["events"]) for row in mine)
        by_arm[arm] = {"turns": len(mine), "lexical_pre_label": totals, "schedule_created": created, "call_cap": capped}
    return {"note": "lexical pre-label from census.py; the registered outcome is the blind two-reviewer label", "arms": by_arm}


def blind_sheet(rows: list[dict[str, Any]], seed: int = 3048) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A shuffled labelling sheet without arm identities, and the key that unblinds it."""
    order = list(range(len(rows)))
    random.Random(seed).shuffle(order)
    sheet, key = [], []
    for position, index in enumerate(order):
        row = rows[index]
        events = [e for e in row["events"] if "meta" not in e and "approval" not in e]
        # Arm C is the only arm with schedule_once; its approval records would name it, so they are
        # dropped from the sheet. The tool trace itself is what the labeller must see (same-turn rule).
        sheet.append({"item": position, "request_id": row["request_id"], "answer": row["answer"], "tool_trace": events,
                      "label": "", "anchored_schedule": None})
        key.append({"item": position, "request_id": row["request_id"], "arm": row["arm"]})
    return sheet, key


def decide(labels: dict[str, dict[str, str]], valid_anchored: dict[str, dict[str, bool]]) -> dict[str, Any]:
    """The registered absolute rule over adjudicated labels: ``labels[arm][request_id]`` is one of
    ``empty | false_claim | unanchored | over_refusal | clarification | valid``."""
    requests = sorted(labels["A"])
    n = len(requests)

    # Counts and whole percentage points, not float rates: 5/10 - 4/10 is 0.0999... in floating
    # point, which read an exact 10-point reduction as missing the registered 10-point threshold.
    def count(arm: str, label: str) -> int:
        return sum(labels[arm][r] == label for r in requests)

    def anchored(arm: str) -> int:
        return sum(bool(valid_anchored[arm].get(r)) for r in requests)

    def at_least(points: int, delta: int) -> bool:  # delta/n >= points/100
        return delta * 100 >= points * n

    b_ok = (at_least(10, count("A", "empty") - count("B", "empty"))
            and (count("B", "over_refusal") - count("A", "over_refusal")) * 100 <= 5 * n)
    c_ok = (at_least(20, count("A", "empty") - count("C", "empty"))
            and count("C", "false_claim") * 100 <= 2 * n
            and (count("C", "over_refusal") - count("A", "over_refusal")) * 100 <= 10 * n)
    if b_ok and c_ok:
        winner = "C" if at_least(10, anchored("C") - anchored("B")) else "B"
    else:
        winner = "C" if c_ok else "B" if b_ok else "none"
    return {"n": n, "B_eligible": b_ok, "C_eligible": c_ok, "winner": winner}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model")
    parser.add_argument("--requests", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--limit", type=int, help="first N requests only (smoke)")
    parser.add_argument("--max-calls", type=int, help="stop before exceeding this many model calls (smoke)")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--blind-sheet", type=Path)
    args = parser.parse_args()
    if args.report:
        print(json.dumps(report(_read_rows(args.report)), ensure_ascii=False, indent=2))
        return
    if args.blind_sheet:
        sheet, key = blind_sheet(_read_rows(args.blind_sheet))
        base = args.blind_sheet.with_suffix("")
        Path(f"{base}.sheet.jsonl").write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in sheet), encoding="utf-8")
        Path(f"{base}.key.jsonl").write_text("".join(json.dumps(k) + "\n" for k in key), encoding="utf-8")
        return
    if not (args.model and args.requests and args.out):
        parser.error("--model, --requests and --out are required for a run")
    requests = load_requests(args.requests)[: args.limit]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="empty-commitments-"))
    # Amendment 1 §1: an empty home, so no owner identity, memory or settings file leaks in.
    os.environ["CHIMERA_HOME"] = str(work / "home")
    for flag in ("CHIMERA_CHAT_STATED_RUNTIME", "CHIMERA_CHAT_SCHEDULE_ONCE", "CHIMERA_CACHE", "CHIMERA_WIRE_LOG"):
        os.environ.pop(flag, None)
    backend = ChatRuntimeBackend(args.model, work / "turns", budget=args.max_calls)
    with args.out.open("w", encoding="utf-8") as handle:
        def write(row: dict[str, object]) -> None:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"{row['request_id']} {row['arm']}: {str(row['answer'])[:90]!r}", flush=True)

        try:
            run(requests, backend, on_row=write)
        except CallCap:
            print(f"stopped: run budget of {args.max_calls} model calls reached", flush=True)
    print(f"model calls: {backend.backend.calls}; turns dir: {work}")


if __name__ == "__main__":
    main()
