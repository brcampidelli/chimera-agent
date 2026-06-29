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

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

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


@dataclass
class PanelResponse:
    """One panel model's answer (or its error)."""

    model: str
    content: str = ""
    error: str | None = None


@dataclass
class FusionTrace:
    """Full record of a fusion run, for inspection and the CLI."""

    panel: list[PanelResponse]
    judge_analysis: str
    final: str

    def successful_panel(self) -> list[PanelResponse]:
        return [r for r in self.panel if r.error is None]


@dataclass
class FusionConfig:
    """Which models play each role, and how the panel runs."""

    panel: list[str]
    judge: str
    synthesizer: str
    max_workers: int = 4
    temperature: float = 0.3

    @classmethod
    def from_settings(cls) -> FusionConfig:
        s = get_settings()
        return cls(panel=list(s.fusion_panel), judge=s.fusion_judge, synthesizer=s.fusion_synthesizer)


def _conversation_text(messages: list[MessageLike]) -> str:
    lines: list[str] = []
    for message in messages:
        data = message.as_dict() if isinstance(message, Message) else message
        role = str(data.get("role", "user"))
        content = str(data.get("content", ""))
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


class FusionEngine:
    """Orchestrates panel -> judge -> synthesizer over a model backend."""

    def __init__(self, backend: SupportsComplete, config: FusionConfig | None = None) -> None:
        self.backend = backend
        self.config = config or FusionConfig.from_settings()

    def run(self, messages: list[MessageLike]) -> FusionTrace:
        panel = self._run_panel(messages)
        judge_analysis = self._run_judge(messages, panel)
        final = self._run_synth(messages, judge_analysis)
        return FusionTrace(panel=panel, judge_analysis=judge_analysis, final=final)

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

        ``tools`` is ignored — fusion is a reasoning backend, not a tool-caller.
        """
        if tools:
            _log.debug("fusion ignores %d tool schema(s); use a single model for tools", len(tools))
        trace = self.run(messages)
        return CompletionResult(content=trace.final, model="fusion")

    # -- stages ------------------------------------------------------------
    def _run_panel(self, messages: list[MessageLike]) -> list[PanelResponse]:
        def call(model: str) -> PanelResponse:
            try:
                result = self.backend.complete(
                    messages, model=model, temperature=self.config.temperature
                )
                return PanelResponse(model=model, content=result.content)
            except Exception as exc:  # one model failing must not sink the panel
                _log.warning("panel model %s failed: %s", model, exc)
                return PanelResponse(model=model, error=str(exc))

        workers = max(1, min(self.config.max_workers, len(self.config.panel)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(call, self.config.panel))

    def _run_judge(self, messages: list[MessageLike], panel: list[PanelResponse]) -> str:
        answers = "\n\n".join(
            f"--- Answer {i} (model {r.model}) ---\n{r.content}"
            for i, r in enumerate(panel, 1)
            if r.error is None
        )
        if not answers:
            return "No panel answers were produced."
        user = f"Task and context:\n{_conversation_text(messages)}\n\nCandidate answers:\n{answers}"
        return self.backend.complete(
            [Message(role="system", content=_JUDGE_SYSTEM), Message(role="user", content=user)],
            model=self.config.judge,
            temperature=0.1,
        ).content

    def _run_synth(self, messages: list[MessageLike], judge_analysis: str) -> str:
        user = (
            f"Original task and context:\n{_conversation_text(messages)}\n\n"
            f"Judge's analysis:\n{judge_analysis}"
        )
        return self.backend.complete(
            [Message(role="system", content=_SYNTH_SYSTEM), Message(role="user", content=user)],
            model=self.config.synthesizer,
            temperature=self.config.temperature,
        ).content
