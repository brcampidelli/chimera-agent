"""The LLM-Fusion engine — Chimera's differentiator.

Runs the same task through a *panel* of models, has a *judge* model produce a
structured analysis of their answers (consensus, contradictions, partial coverage,
unique insights, blind spots), then a *synthesizer* writes the final answer grounded
in that analysis. The lift comes from the synthesis step itself, not only model
diversity (per OpenRouter Fusion's findings).

``FusionEngine`` implements :class:`~chimera.providers.gateway.SupportsComplete`, so
it is a drop-in *reasoning* backend anywhere a model is expected. It does not do
tool-calling — fusion is for hard reasoning/synthesis; tool turns stay single-model
(see :mod:`chimera.fusion.router`).
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from chimera.config import get_settings
from chimera.providers.gateway import CompletionResult, Message, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("fusion.engine")

_JUDGE_SYSTEM = (
    "You are an impartial judge. You are given several independent answers to the "
    "same task. Analyze them — do NOT write a final answer yourself. Produce a "
    "concise, structured analysis with these sections: Consensus, Contradictions, "
    "Partial coverage, Unique insights, Blind spots."
)
_SYNTH_SYSTEM = (
    "You are a synthesizer. Using the original task and the judge's structured "
    "analysis of several candidate answers, write the single best final answer. "
    "Resolve contradictions, fold in unique insights, and avoid the blind spots. "
    "Answer the task directly; do not mention the panel or the judge."
)
_SYNTH_AGREED_SYSTEM = (
    "You are a synthesizer. Several independent answers to the task agree closely. "
    "Using the original task and those answers, write the single best final answer. "
    "Answer the task directly; do not mention that there were multiple answers."
)


def _sum_opt(values: Iterable[int | None]) -> int | None:
    """Sum the reported values; ``None`` if none were reported (never fabricate 0)."""
    reported = [v for v in values if v is not None]
    return sum(reported) if reported else None


def _normalize_ws(text: str) -> str:
    """Lowercase and collapse whitespace, for a lexical similarity comparison."""
    return " ".join(text.split()).lower()


@dataclass
class PanelResponse:
    """One panel model's answer (or its error)."""

    model: str
    content: str = ""
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class StageUsage:
    """Token usage for one fusion stage (a panel model, the judge, or the synthesizer)."""

    stage: Literal["panel", "judge", "synth"]
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class FusionTrace:
    """Full record of a fusion run, for inspection and the CLI."""

    panel: list[PanelResponse]
    judge_analysis: str
    final: str
    usage: list[StageUsage] = field(default_factory=list)
    early_stopped: bool = False  # selective mode: probe agreed, panel+judge short-circuited
    aggregation: Literal["synth", "vote"] = "synth"  # task-typed routing: synthesize vs majority-vote

    def successful_panel(self) -> list[PanelResponse]:
        return [r for r in self.panel if r.error is None]

    def panel_diversity(self) -> float | None:
        """Mean pairwise dissimilarity (0..1) of the successful panel answers — the panel-independence
        axis (MALLM / blind-panel, arXiv 2607.05477 + 2607.02507).

        The fusion panel is blind by construction — each model answers the same prompt with no sight of
        the others (:meth:`FusionEngine._run_panel`), so its answers are independent. This quantifies how
        much that independence *paid off*: high diversity means the panel brought genuinely different
        perspectives (synthesis has material to work with); low diversity means it converged (agreement —
        the cheap early-stop / vote territory). ``None`` with fewer than two successful answers.
        """
        texts = [_normalize_ws(r.content) for r in self.panel if r.error is None]
        if len(texts) < 2:
            return None
        dissims = [
            1.0 - difflib.SequenceMatcher(None, a, b).ratio()
            for i, a in enumerate(texts)
            for b in texts[i + 1 :]
        ]
        return sum(dissims) / len(dissims)

    def prompt_tokens(self) -> int | None:
        """Total input tokens across stages, or ``None`` if no stage reported usage."""
        return _sum_opt(u.prompt_tokens for u in self.usage)

    def completion_tokens(self) -> int | None:
        """Total output tokens across stages, or ``None`` if no stage reported usage."""
        return _sum_opt(u.completion_tokens for u in self.usage)

    def total_tokens(self) -> int | None:
        """Prompt + completion tokens, or ``None`` if usage was never reported."""
        p, c = self.prompt_tokens(), self.completion_tokens()
        if p is None and c is None:
            return None
        return (p or 0) + (c or 0)

    def by_stage(self) -> dict[str, tuple[int, int]]:
        """Aggregate ``(prompt, completion)`` tokens per stage name."""
        agg: dict[str, tuple[int, int]] = {}
        for u in self.usage:
            p, c = agg.get(u.stage, (0, 0))
            agg[u.stage] = (p + (u.prompt_tokens or 0), c + (u.completion_tokens or 0))
        return agg


@dataclass
class FusionConfig:
    """Which models play each role, and how the panel runs."""

    panel: list[str]
    judge: str
    synthesizer: str
    max_workers: int = 4
    temperature: float = 0.3
    mode: Literal["full", "selective"] = "full"
    probe_k: int = 2
    agreement_threshold: float = 0.8
    # Task-typed aggregation (MALLM, arXiv 2607.05477): when on, a logic/single-answer task on which
    # the panel reaches a clear majority is aggregated by VOTE (skipping judge+synth) rather than
    # synthesized — a correct minority answer isn't averaged away, and it's cheaper. Off by default:
    # every other task, and any logic task without a majority, still uses judge -> synthesizer.
    task_typed: bool = False
    vote_threshold: float = 0.85

    @classmethod
    def from_settings(cls) -> FusionConfig:
        s = get_settings()
        mode: Literal["full", "selective"] = (
            "selective" if s.fusion_mode == "selective" else "full"
        )
        return cls(
            panel=list(s.fusion_panel),
            judge=s.fusion_judge,
            synthesizer=s.fusion_synthesizer,
            mode=mode,
            probe_k=s.fusion_probe_k,
            agreement_threshold=s.fusion_agreement_threshold,
            task_typed=s.fusion_task_typed,
        )


def _content_text(content: object) -> str:
    """The TEXT of a message's content — never a stringified multimodal list.

    A vision turn's content is a list of parts ({"type":"text",...}, {"type":"image_url",...} with a
    base64 data URL). ``str()`` on that would dump the base64 blob into the judge/synth prompt (token
    blow-up + nonsense). Join only the text parts instead.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
    return str(content) if content else ""


def _conversation_text(messages: list[MessageLike]) -> str:
    lines: list[str] = []
    for message in messages:
        data = message.as_dict() if isinstance(message, Message) else message
        role = str(data.get("role", "user"))
        content = _content_text(data.get("content", ""))
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


class FusionEngine:
    """Orchestrates panel -> judge -> synthesizer over a model backend."""

    def __init__(self, backend: SupportsComplete, config: FusionConfig | None = None) -> None:
        self.backend = backend
        self.config = config or FusionConfig.from_settings()

    def run(self, messages: list[MessageLike]) -> FusionTrace:
        if self.config.mode == "selective" and len(self.config.panel) >= 2:
            return self._run_selective(messages)
        return self._run_full(messages)

    def _run_full(self, messages: list[MessageLike]) -> FusionTrace:
        _log.debug("fusion engaged: %d-model panel -> judge -> synthesizer", len(self.config.panel))
        panel = self._run_panel(messages)
        analysis, final, aggregation, judge, synth = self._aggregate(messages, panel)
        trace = FusionTrace(
            panel=panel,
            judge_analysis=analysis,
            final=final,
            usage=self._collect_usage(panel, judge, synth),
            aggregation=aggregation,
        )
        self._log_usage(trace)
        return trace

    def _aggregate(
        self, messages: list[MessageLike], panel: list[PanelResponse]
    ) -> tuple[str, str, Literal["synth", "vote"], CompletionResult | None, CompletionResult | None]:
        """Aggregate the panel into a final answer, routing by task type when enabled.

        Returns ``(judge_analysis, final, aggregation, judge_result, synth_result)``. For a
        logic-typed task on which the panel reaches a clear majority, aggregates by VOTE (no judge
        or synthesizer call — the majority answer *is* the final); otherwise runs the judge ->
        synthesizer path. The vote branch is conservative: it needs ≥2 successful panel answers and a
        real majority cluster, else it falls through to synthesis (today's behaviour).
        """
        ok = [r for r in panel if r.error is None]
        if self.config.task_typed and len(ok) >= 2:
            from chimera.fusion.task_type import classify_task_type

            if classify_task_type(messages) == "logic":
                from chimera.fusion.consistency import majority

                winner = majority([r.content for r in ok], threshold=self.config.vote_threshold)
                if winner is not None:
                    _log.debug(
                        "fusion task-typed: logic task with panel majority -> vote (skipped judge+synth)"
                    )
                    return "", winner, "vote", None, None
        judge = self._run_judge(messages, panel)
        synth = self._run_synth(messages, judge.content)
        return judge.content, synth.content, "synth", judge, synth

    def _run_selective(self, messages: list[MessageLike]) -> FusionTrace:
        """Probe a few models first; short-circuit on agreement, else escalate to full.

        Agreement is a cheap local text-similarity check (no extra model call), so a
        disagreeing turn costs exactly the same as full fusion while an agreeing turn
        skips the rest of the panel and the judge. The synthesis step is always kept —
        it is where the lift comes from.
        """
        k = max(2, min(self.config.probe_k, len(self.config.panel)))
        probe = self._run_panel(messages, self.config.panel[:k])
        ok = [r for r in probe if r.error is None]
        if len(ok) >= 2 and self._agree(ok):
            _log.debug("fusion early-stop: %d probe models agreed", len(ok))
            agreed = self._run_synth_agreed(messages, ok)
            trace = FusionTrace(
                panel=probe,
                judge_analysis="",
                final=agreed.content,
                usage=self._collect_usage(probe, None, agreed),
                early_stopped=True,
            )
            self._log_usage(trace)
            return trace
        rest = self._run_panel(messages, self.config.panel[k:])
        panel = probe + rest
        analysis, final, aggregation, judge, synth = self._aggregate(messages, panel)
        trace = FusionTrace(
            panel=panel,
            judge_analysis=analysis,
            final=final,
            usage=self._collect_usage(panel, judge, synth),
            aggregation=aggregation,
        )
        self._log_usage(trace)
        return trace

    def _agree(self, responses: list[PanelResponse]) -> bool:
        """True when every pair of probe answers is at least ``agreement_threshold`` similar."""
        texts = [_normalize_ws(r.content) for r in responses]
        ratios = [
            difflib.SequenceMatcher(None, a, b).ratio()
            for i, a in enumerate(texts)
            for b in texts[i + 1 :]
        ]
        return bool(ratios) and min(ratios) >= self.config.agreement_threshold

    # -- SupportsComplete --------------------------------------------------
    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        """Run the fusion pipeline and return the synthesized answer.

        ``tools`` is ignored — fusion is a reasoning backend, not a tool-caller. ``temperature`` and
        ``max_tokens`` are ALSO ignored on purpose: fusion is a multi-stage pipeline with its own
        per-stage sampling (a diverse panel, a low-temperature judge, a synthesizer — all from
        ``self.config``), so a single protocol-level temperature has no coherent meaning here. They
        stay in the signature only for :class:`SupportsComplete` compatibility.
        """
        if tools:
            _log.debug("fusion ignores %d tool schema(s); use a single model for tools", len(tools))
        trace = self.run(messages)
        route_meta = {
            "kind": "fusion",
            "aggregation": trace.aggregation,
            "early_stopped": trace.early_stopped,
            "diversity": trace.panel_diversity(),
            "panel": [
                {
                    "model": r.model,
                    "content": r.content,
                    "error": r.error,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                }
                for r in trace.panel
            ],
            "judge_analysis": trace.judge_analysis,
            "stages": [
                {
                    "stage": u.stage,
                    "model": u.model,
                    "prompt_tokens": u.prompt_tokens,
                    "completion_tokens": u.completion_tokens,
                }
                for u in trace.usage
            ],
        }
        return CompletionResult(
            content=trace.final,
            model="fusion",
            prompt_tokens=trace.prompt_tokens(),
            completion_tokens=trace.completion_tokens(),
            route_meta=route_meta,
        )

    # -- stages ------------------------------------------------------------
    def _run_panel(
        self, messages: list[MessageLike], models: list[str] | None = None
    ) -> list[PanelResponse]:
        panel_models = models if models is not None else self.config.panel

        def call(model: str) -> PanelResponse:
            try:
                result = self.backend.complete(
                    messages, model=model, temperature=self.config.temperature
                )
                return PanelResponse(
                    model=model,
                    content=result.content,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                )
            except Exception as exc:  # one model failing must not sink the panel
                _log.warning("panel model %s failed: %s", model, exc)
                return PanelResponse(model=model, error=str(exc))

        workers = max(1, min(self.config.max_workers, len(panel_models)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(call, panel_models))

    def _run_judge(
        self, messages: list[MessageLike], panel: list[PanelResponse]
    ) -> CompletionResult:
        answers = "\n\n".join(
            f"--- Answer {i} (model {r.model}) ---\n{r.content}"
            for i, r in enumerate(panel, 1)
            if r.error is None
        )
        if not answers:
            return CompletionResult(content="No panel answers were produced.", model=self.config.judge)
        user = f"Task and context:\n{_conversation_text(messages)}\n\nCandidate answers:\n{answers}"
        return self.backend.complete(
            [Message(role="system", content=_JUDGE_SYSTEM), Message(role="user", content=user)],
            model=self.config.judge,
            temperature=0.1,
        )

    def _run_synth(self, messages: list[MessageLike], judge_analysis: str) -> CompletionResult:
        user = (
            f"Original task and context:\n{_conversation_text(messages)}\n\n"
            f"Judge's analysis:\n{judge_analysis}"
        )
        return self.backend.complete(
            [Message(role="system", content=_SYNTH_SYSTEM), Message(role="user", content=user)],
            model=self.config.synthesizer,
            temperature=self.config.temperature,
        )

    def _run_synth_agreed(
        self, messages: list[MessageLike], answers: list[PanelResponse]
    ) -> CompletionResult:
        """Synthesize directly from agreeing probe answers (no judge step)."""
        joined = "\n\n".join(
            f"--- Answer {i} (model {r.model}) ---\n{r.content}" for i, r in enumerate(answers, 1)
        )
        user = (
            f"Original task and context:\n{_conversation_text(messages)}\n\n"
            f"Agreeing answers:\n{joined}"
        )
        return self.backend.complete(
            [Message(role="system", content=_SYNTH_AGREED_SYSTEM), Message(role="user", content=user)],
            model=self.config.synthesizer,
            temperature=self.config.temperature,
        )

    # -- telemetry ---------------------------------------------------------
    def _collect_usage(
        self,
        panel: list[PanelResponse],
        judge: CompletionResult | None,
        synth: CompletionResult | None,
    ) -> list[StageUsage]:
        usage: list[StageUsage] = [
            StageUsage("panel", r.model, r.prompt_tokens, r.completion_tokens)
            for r in panel
            if r.error is None
        ]
        if judge is not None:
            usage.append(
                StageUsage("judge", judge.model, judge.prompt_tokens, judge.completion_tokens)
            )
        if synth is not None:
            usage.append(
                StageUsage("synth", synth.model, synth.prompt_tokens, synth.completion_tokens)
            )
        return usage

    def _log_usage(self, trace: FusionTrace) -> None:
        by = trace.by_stage()

        def fmt(stage: str) -> str:
            p, c = by.get(stage, (0, 0))
            return f"{p}/{c}"

        _log.info(
            "fusion tokens (in/out) panel=%s judge=%s synth=%s total=%s",
            fmt("panel"),
            fmt("judge"),
            fmt("synth"),
            trace.total_tokens(),
        )
