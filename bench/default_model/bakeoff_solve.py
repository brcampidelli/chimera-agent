"""One solve of the default-model bake-off: one SWE-bench instance, one arm, one fresh checkout.

Run by `bakeoff.py` as a subprocess so the wall clock can kill it; never by hand in a paid session.

    python bakeoff_solve.py <instance.json> <arm> <out.json>

The workspace recipe, the task text and the tool list are `bench/prompt_overlays/solve_one.py`'s,
imported rather than copied so both benches run the same instrument. That module reads
`H45_DJANGO_REF` and `H45_WORK` (and refuses a work dir whose name lacks "h45"); the driver points
both at this bench's own directories.

Writes one JSON object: the patch (`git diff base_commit`), the loop's counters, every model call's
usage, served provider, finish reason and billed cost, the tool results that were errors, and the
shell commands the agent ran.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO, REPO / "bench" / "prompt_overlays", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import solve_one as po  # noqa: E402  — bench/prompt_overlays: workspace recipe, task text, tools
from bakeoff_arms import ARMS, MAX_STEPS, Arm, assert_frozen  # noqa: E402

#: The raw usage of the call in flight, read off LiteLLM's response. The gateway normalises the
#: response and keeps no billed cost; OpenRouter returns it when asked (`usage.include`).
_TAP: list[dict[str, Any]] = []


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None) or {}
        value = extra.get(name)
    return value


def _install_tap() -> None:
    import litellm

    real = litellm.completion

    def tapped(*args: Any, **kwargs: Any) -> Any:
        resp = real(*args, **kwargs)
        usage = _field(resp, "usage")
        details = _field(usage, "completion_tokens_details")
        _TAP.append({"billed": _field(usage, "cost"), "reasoning": _field(details, "reasoning_tokens"),
                     "gen_id": _field(resp, "id")})
        return resp

    litellm.completion = tapped


class _DroppedCalls(logging.Handler):
    """Counts tool calls the gateway dropped because their argument JSON did not parse."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "unparseable arguments" in record.getMessage():
            self.count += 1


class Recorder:
    """The gateway pinned to the arm's provider with no fallbacks, recording every call."""

    def __init__(self, arm: Arm) -> None:
        from chimera.providers import LLMGateway

        self.gateway = LLMGateway()
        if self.gateway.settings.fallback_models:
            raise SystemExit(f"fallback models configured: {self.gateway.settings.fallback_models}")
        self.arm = arm
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        kwargs["extra_body"] = {"provider": {"order": [self.arm.provider], "allow_fallbacks": False},
                                "usage": {"include": True}}
        started = time.monotonic()
        before = len(_TAP)
        result = self.gateway.complete(messages, **kwargs)
        raw = _TAP[-1] if len(_TAP) > before else {}
        self.calls.append({
            "prompt": result.prompt_tokens, "completion": result.completion_tokens,
            "cache_read": result.cache_read_tokens, "provider": result.provider,
            "finish": result.finish_reason, "temperature": kwargs.get("temperature"),
            "tools_offered": bool(kwargs.get("tools")), "tool_calls": len(result.tool_calls or []),
            "empty": not (result.content or "").strip() and not result.tool_calls,
            "billed": raw.get("billed"), "reasoning": raw.get("reasoning"), "gen_id": raw.get("gen_id"),
            "seconds": round(time.monotonic() - started, 2),
        })
        return result


def tool_errors(transcript: list[Any]) -> dict[str, int]:
    """Tool results the loop returned as errors, by kind (from the agent's own transcript)."""
    out = {"unknown_tool": 0, "bad_arguments": 0, "other": 0}
    for msg in transcript:
        if not isinstance(msg, dict):
            msg = getattr(msg, "as_dict", lambda: {})()
        if msg.get("role") != "tool":
            continue
        text = str(msg.get("content") or "")
        if not text.startswith("error:"):
            continue
        if text.startswith("error: unknown tool"):
            out["unknown_tool"] += 1
        elif "argument" in text and ("unexpected keyword" in text or "missing" in text):
            out["bad_arguments"] += 1
        else:
            out["other"] += 1
    return out


def main() -> None:
    inst_path, arm_name, out_path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    assert_frozen()
    arm = ARMS[arm_name]
    inst = json.loads(Path(inst_path).read_text(encoding="utf-8"))
    row: dict[str, Any] = {"instance_id": inst["instance_id"], "arm": arm_name, "model": arm.model,
                           "pinned": arm.provider, "halted": None}
    started = time.monotonic()
    ws: Path | None = None
    recorder: Recorder | None = None
    dropped = _DroppedCalls()
    logging.getLogger("chimera.providers.gateway").addHandler(dropped)
    try:
        _install_tap()
        ws = po.prepare_workspace(inst, arm_name)
        from chimera.core.agent import DEFAULT_SYSTEM_PROMPT, Agent, AgentConfig
        from chimera.governance.allowlist import restrict_registry
        from chimera.tools.builtin import default_registry

        recorder = Recorder(arm)
        tools = restrict_registry(default_registry(ws, host_exec_confirm=None), allow=po.CODING_TOOLS)
        # The solve worker's config (chimera/cli/main.py, `_worker_cfg`) as bench/prompt_overlays
        # runs it: the default system prompt and temperature, no owner instructions, no budget.
        config = AgentConfig(model=arm.model, max_steps=MAX_STEPS, insist_on_action=True,
                             project_root=ws, turn_context=True, prefix_nonce="")
        agent = Agent(recorder, tools, config)  # type: ignore[arg-type]
        task = po.INSTRUCTION.format(problem=inst["problem_statement"])
        system = agent.compose_system_prompt(task)
        row["system_sha256"] = hashlib.sha256(system.encode("utf-8")).hexdigest()
        row["system_starts_with_default"] = system.startswith(DEFAULT_SYSTEM_PROMPT)
        row["tools"] = sorted(t.name for t in tools.tools())
        result = agent.run(task)
        answer = result.answer or ""
        row.update({
            "steps": result.steps, "stopped": result.stopped_reason, "tool_calls": result.tool_calls_made,
            "tool_names": result.tool_names, "answer": answer[:600],
            "answer_empty": not answer.strip(),
            "answer_empty_note": answer.startswith("(No final answer: the model returned an empty reply"),
            "tool_errors": tool_errors(result.transcript),
            "agent_usd": result.usd,
            "shell": po.shell_commands(result.transcript),
        })
    except Exception as exc:  # noqa: BLE001 — a provider or harness failure is a halt (PROTOCOL §2)
        row["halted"] = f"{type(exc).__name__}: {exc}"[:400]
        row["traceback"] = traceback.format_exc()[-1500:]
    finally:
        row["dropped_tool_calls"] = dropped.count
        if ws is not None and ws.exists():
            diff = po._run(["git", "-C", str(ws), "diff", inst["base_commit"]])
            row["patch"] = diff.stdout if diff.returncode == 0 else ""
        else:
            row.setdefault("patch", "")
        row["calls"] = recorder.calls if recorder else []
        row["seconds"] = round(time.monotonic() - started, 1)
        out_path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
