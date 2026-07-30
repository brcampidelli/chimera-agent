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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.core.context_drift import DriftReport

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
    #: Prompt tokens the provider served from its cache — the part of ``prompt_tokens`` that was
    #: roughly a tenth of the price. None when the provider reports no cache usage at all, which is
    #: NOT the same as a miss and must not be counted as one. Defaulting to None rather than 0 means
    #: a call site that forgets it reports "unknown", never a cache miss that did not happen.
    cached_tokens: int | None = None
    #: The assistant's text for this step, clipped. Empty on a pure tool-call step.
    content: str = ""
    tools: list[ToolRecord] = field(default_factory=list)
    #: Whether the context was compacted right after this step. Marks where history was dropped,
    #: which is the first thing to check when an agent starts contradicting its earlier self.
    compacted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "model": self.model,
            "content": self.content,
            "compacted": self.compacted,
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
    def compactions(self) -> int:
        """How many times history was dropped during the run."""
        return sum(1 for s in self.steps if s.compacted)

    @property
    def cache_hit_rate(self) -> float | None:
        """Share of prompt tokens the provider served from cache, across the whole run.

        The single most useful cost number a loop produces, because a cached prompt token costs
        roughly a tenth of a fresh one — so the same run can differ ~10x in price on this alone. It
        is also a design signal: the rate collapses when something rewrites the front of the prompt
        (a mutated system message, a re-ordered tool list, a timestamp in the preamble), which is a
        bug you cannot see any other way.

        Returns None when NO step reported cache usage — the provider is silent, not missing. A
        silent provider scored as 0% would read as a broken cache and invite someone to go fix a
        prefix that was never the problem.
        """
        reported = [s for s in self.steps if s.cached_tokens is not None]
        if not reported:
            return None
        prompt = sum(s.prompt_tokens for s in reported)
        if prompt <= 0:
            return None
        return sum(s.cached_tokens or 0 for s in reported) / prompt

    @property
    def drift(self) -> DriftReport:
        """Whether this trajectory stopped getting anywhere. See :mod:`chimera.core.context_drift`.

        Short runs come back ``assessed=False`` — which is the honest answer, not a clean bill of
        health. With the default eight-step budget that is every run: drift needs a trajectory long
        enough to have a shape, which is the configuration the context budget made survivable.
        """
        from chimera.core.context_drift import assess

        return assess(self.steps)

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
            "cache_hit_rate": (
                None if self.cache_hit_rate is None else round(self.cache_hit_rate, 3)
            ),
            "compactions": self.compactions,
            # In the trace itself, so a run is self-describing: whoever reads this back should not
            # have to re-derive by hand whether the trajectory was still going somewhere.
            "drift": self.drift.as_dict(),
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
