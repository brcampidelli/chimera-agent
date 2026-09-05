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
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.core.redact import redact

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.core.context_drift import DriftReport

# Enough to recognise a call and see how a result begins or ends; far too little to reconstruct a
# 20k-char file dump. Diffs and verifier output already have their own richer receipts.
_ARG_CHARS = 400
_OBS_CHARS = 800


#: A trace that grows without bound on a machine that runs for weeks stops being a diagnostic and
#: becomes a disk problem — and the first symptom is a daemon that cannot write anything at all.
#: One previous generation is kept: enough to span a rotation while investigating an incident,
#: bounded enough that the cap means what it says.
MAX_TRACE_BYTES = 32 * 1024 * 1024


def _rotate_if_large(path: Path) -> None:
    """Move the trace aside once it passes the cap, keeping exactly one previous generation.

    Rename rather than trim: rewriting the file to drop old lines would have to read all of it, and
    a crash mid-rewrite loses the whole history instead of the oldest part of it.
    """
    try:
        if path.exists() and path.stat().st_size >= MAX_TRACE_BYTES:
            path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:  # pragma: no cover - disk-shaped failure
        # A rotation that fails must not take the run down: the answer is the product, the trace is
        # evidence about it.
        pass


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
    #: Wall-clock milliseconds spent in the model call for this step. 0 when nobody measured it.
    #:
    #: MEASURED, not derived. Every other number here comes from the provider's usage report, and
    #: this one cannot: a rate needs a duration, and no provider sends one. Deriving it from the
    #: run's total time would divide by the tool calls, the verifier and the user's thinking time —
    #: producing a number that moves when the network is slow and calling it the model's speed.
    elapsed_ms: int = 0
    #: Which BACKEND served this step, when the router reports one — beside `model`, not
    #: instead of it.
    #:
    #: A slug is a pool. `bench/context_rot` measured three consecutive calls to one OpenRouter
    #: slug answered by `Wafer`, `Inceptron` and `DigitalOcean`, so a trace that records only
    #: the model records the pool and calls it a machine. Every census this project has run over
    #: these traces — batch sizes per family, context peaks, cache hit rates — attributed to a
    #: model what may belong to whichever backend happened to answer.
    #:
    #: Empty on the streaming path, where the chunks carry nothing to read it from.
    provider: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "provider": self.provider,
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
    def generation_ms(self) -> int:
        """Wall-clock time spent waiting on the model, across the run.

        Not the run's duration. The difference between the two is the tools, the verifier and the
        time a paused run spent waiting for a person — and attributing any of that to the model is
        how a fast model on a slow machine gets blamed for the machine.
        """
        return sum(s.elapsed_ms for s in self.steps)

    @property
    def tokens_per_second(self) -> float | None:
        """Output tokens per second of time spent inside model calls.

        None rather than zero when nothing was measured, and the distinction is the whole point:
        a provider that reports no completion tokens, or a caller that never timed its steps, has
        told us nothing about speed. Zero would say the model produced nothing, which is a claim.

        Counts only steps that reported BOTH a duration and a token count, because a step missing
        either would otherwise drag the rate toward a number no component actually produced.

        What it measures is deliberately the honest, slightly pessimistic thing: wall time from
        request to last token, so the network and the time-to-first-token are in it. That is the
        rate a person waiting at the screen experiences, which is the one worth reporting.
        """
        counted = [s for s in self.steps if s.elapsed_ms > 0 and s.completion_tokens > 0]
        if not counted:
            return None
        seconds = sum(s.elapsed_ms for s in counted) / 1000
        if seconds <= 0:
            return None
        return sum(s.completion_tokens for s in counted) / seconds

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

    def write(self, path: Path, *, task: str, stopped_reason: str, run_id: str = "") -> str:
        """Append this run's trace as one JSONL line. Returns the id it was written under.

        One line per run rather than per step: a run is the unit anyone reads back, and a partial
        run interleaved with others is worse than no trace at all.

        **`run_id` and `ts` are what make the line joinable.** The record used to be keyed by a
        truncated task plus a stop reason, which collides exactly where it matters: a bench runs the
        same task dozens of times, and one run with three attempts writes three lines that are
        indistinguishable. Nothing downstream — a success×context curve, a cron replay, failure
        attribution — can be built on a key that cannot tell two runs apart.

        The body is **redacted** and the file is **capped**, in the same place, because both are
        properties of writing a trace to disk rather than of producing one. On the 24/7 path this
        text came from a broker and a payment processor; see :mod:`chimera.core.redact` for exactly
        what that promises and what it does not.
        """
        run_id = run_id or uuid.uuid4().hex
        record = {
            "run_id": run_id,
            "ts": datetime.now(UTC).isoformat(),
            "task": clip(task, _ARG_CHARS),
            "stopped_reason": stopped_reason,
            **self.as_dict(),
        }
        line = redact(json.dumps(record, ensure_ascii=False))
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_large(path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return run_id


def tool_record(name: str, arguments: dict[str, Any], observation: str) -> ToolRecord:
    """Build a clipped record. `ok` follows the loop's own convention: a tool failed if it errored
    OR if a gate refused to run it.

    The second half was missing here long after `agent.py` and `registry.py` had it, and the
    consequence landed exactly where it hurts most: `traces.jsonl` is the artefact someone opens
    *after* a run went wrong, and it was the last place still recording a refused tool as a
    completed one. `is_refusal` deliberately excludes `[idempotent: …]` — that marker means the
    effect already happened and is not being repeated, which is a success, not a refusal.
    """
    from chimera.tools.base import is_refusal  # lazy: `chimera.tools` resolves names on first use

    try:
        args = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        args = repr(arguments)
    return ToolRecord(
        name=name,
        arguments=clip(args, _ARG_CHARS),
        observation=clip(observation, _OBS_CHARS),
        ok=not observation.startswith("error:") and not is_refusal(observation),
    )
