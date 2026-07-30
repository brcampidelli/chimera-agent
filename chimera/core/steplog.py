"""Per-step accounting for an agent run — how big the context got, and what actually happened.

The loop used to report only a run-level token sum and, downstream, a list of ``{tool, ok}`` pairs.
That is enough to say a run cost money and that some tool failed; it is not enough to answer either
of the two questions that matter when a run goes wrong:

    "how much context was this run carrying when it started to drift?"
    "what did the model actually ask for, and what came back?"

Both are recoverable at almost no cost, because the provider already reports ``prompt_tokens`` on
every call — and that number *is* the size of the context at that step. No tokenizer needed, no
extra API call, no estimate. What was missing was simply keeping it per step instead of summing it
away.

Bodies are truncated hard on the way in. A trace that costs as much to store as the run costs to
produce will be turned off, and a trace that is turned off explains nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Enough to recognise a call and see how a result begins or ends; far too little to reconstruct a
# 20k-char file dump. Diffs and verifier output already have their own richer receipts.
_ARG_CHARS = 400
_OBS_CHARS = 800


def clip(text: str, limit: int) -> str:
    """Head+tail, so a truncated observation still shows how it ended (errors live at the end)."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n…[{len(text) - limit} chars elided]…\n{text[-tail:]}"


@dataclass
class ToolRecord:
    """One tool call, as issued and as answered."""

    name: str
    arguments: str  # JSON, clipped
    observation: str  # clipped
    ok: bool


@dataclass
class StepRecord:
    """One turn of the loop: a model call plus whatever tools it triggered."""

    index: int
    #: Tokens the provider counted for THIS call's prompt — i.e. the live size of the context.
    #: 0 when the backend reported nothing (some providers omit usage on streamed calls).
    prompt_tokens: int
    completion_tokens: int
    model: str
    #: The assistant's text for this step, clipped. Empty on a pure tool-call step.
    content: str = ""
    tools: list[ToolRecord] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model": self.model,
            "content": self.content,
            "tools": [
                {"name": t.name, "arguments": t.arguments, "observation": t.observation, "ok": t.ok}
                for t in self.tools
            ],
        }


@dataclass
class StepLog:
    """The per-step record of one run, plus the context high-water mark."""

    steps: list[StepRecord] = field(default_factory=list)

    def add(self, step: StepRecord) -> None:
        self.steps.append(step)

    @property
    def context_peak_tokens(self) -> int:
        """The largest prompt this run sent.

        This is the number to watch: it is what a context budget is spent against, and it is what
        decides whether raising ``max_steps`` is safe or is the thing that will break the run.
        """
        return max((s.prompt_tokens for s in self.steps), default=0)

    @property
    def context_growth_per_step(self) -> float:
        """Mean tokens added to the prompt per step, from the first measured step to the last.

        Lets a caller answer "how many more steps fit?" instead of finding out by hitting the wall.
        Returns 0.0 when fewer than two steps reported usage.
        """
        measured = [s.prompt_tokens for s in self.steps if s.prompt_tokens > 0]
        if len(measured) < 2:
            return 0.0
        return (measured[-1] - measured[0]) / (len(measured) - 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_peak_tokens": self.context_peak_tokens,
            "context_growth_per_step": round(self.context_growth_per_step, 1),
            "steps": [s.as_dict() for s in self.steps],
        }

    def write(self, path: Path, *, task: str, stopped_reason: str) -> None:
        """Append this run's trace as one JSONL line.

        One line per run rather than per step: a run is the unit anyone reads back, and a partial
        run interleaved with others is worse than no trace at all.
        """
        record = {"task": clip(task, _ARG_CHARS), "stopped_reason": stopped_reason, **self.as_dict()}
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def tool_record(name: str, arguments: dict[str, Any], observation: str) -> ToolRecord:
    """Build a clipped record. `ok` follows the loop's own convention: an observation that starts
    with "error:" is what every other consumer in the codebase already treats as a failure."""
    try:
        args = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        args = repr(arguments)
    return ToolRecord(
        name=name,
        arguments=clip(args, _ARG_CHARS),
        observation=clip(observation, _OBS_CHARS),
        ok=not observation.startswith("error:"),
    )
