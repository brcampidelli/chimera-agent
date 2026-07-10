"""Blind-panel guard + panel-independence metric (MALLM / blind panel, arXiv 2607.05477 + 2607.02507).

The fusion panel is blind *by construction* — each model answers the same prompt independently, never
seeing another member's draft. These tests (a) guard that invariant against regressions and (b) exercise
the ``panel_diversity`` observability metric (the "panel independence" axis the sweep called for).
"""

from __future__ import annotations

from typing import Any

from chimera.fusion import FusionConfig, FusionEngine
from chimera.providers import CompletionResult

CONFIG = FusionConfig(panel=["alpha", "beta", "gamma"], judge="judge", synthesizer="synth", mode="full")


class RecordingBackend:
    """Records the exact prompt each model received, so we can assert panel blindness."""

    def __init__(self) -> None:
        self.prompts: dict[str, str] = {}

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        rendered = "\n".join(
            str((m.as_dict() if hasattr(m, "as_dict") else m).get("content", "")) for m in messages
        )
        self.prompts[str(model)] = rendered
        if model == "judge":
            return CompletionResult(content="JUDGE", model="judge")
        if model == "synth":
            return CompletionResult(content="FINAL", model="synth")
        # Each panel model emits a distinctive marker so we can detect cross-contamination.
        return CompletionResult(content=f"ANSWER_FROM_{model}", model=str(model))


def test_panel_members_never_see_each_others_answers() -> None:
    backend = RecordingBackend()
    FusionEngine(backend, CONFIG).run([{"role": "user", "content": "solve it"}])
    panel_models = ["alpha", "beta", "gamma"]
    for model in panel_models:
        prompt = backend.prompts[model]
        for other in panel_models:
            if other != model:
                assert f"ANSWER_FROM_{other}" not in prompt, (
                    f"{model}'s prompt leaked {other}'s answer — panel is no longer blind"
                )


class DiversityBackend:
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        if model in ("judge", "synth"):
            return CompletionResult(content="X", model=str(model))
        return CompletionResult(content=self.answers[str(model)], model=str(model))


def test_panel_diversity_high_when_answers_differ() -> None:
    trace = FusionEngine(
        DiversityBackend({"alpha": "the answer is red", "beta": "completely different text here", "gamma": "yet another unrelated response"}),
        CONFIG,
    ).run([{"role": "user", "content": "q"}])
    div = trace.panel_diversity()
    assert div is not None and div > 0.5


def test_panel_diversity_low_when_answers_agree() -> None:
    trace = FusionEngine(
        DiversityBackend({"alpha": "same answer", "beta": "same answer", "gamma": "same answer"}),
        CONFIG,
    ).run([{"role": "user", "content": "q"}])
    div = trace.panel_diversity()
    assert div is not None and div < 0.05


def test_panel_diversity_none_with_one_answer() -> None:
    cfg = FusionConfig(panel=["alpha"], judge="judge", synthesizer="synth", mode="full")
    trace = FusionEngine(DiversityBackend({"alpha": "solo"}), cfg).run([{"role": "user", "content": "q"}])
    assert trace.panel_diversity() is None
