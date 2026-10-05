"""A judge or synthesiser failure falls back to the panel instead of losing it (study 30, S30-02).

The panel runs inside a try/except — "one model failing must not sink the panel" — but the judge
and the synthesiser did not. A judge that raised (a 429, a timeout, a provider 500) propagated out of
``run()`` and threw away every panel answer that had already been paid for; a synthesiser that came
back empty twice shipped ``final=""`` as the fused answer. ``verified.py`` already states the rule
the engine broke: a verifier failure never loses the answer.

And the opposite case was the worse one: when EVERY panelist failed, the judge was handed the canned
text "No panel answers were produced." and the synthesiser still wrote a final answer — a single
frontier model answering unpanelled, labelled ``model="fusion"``. With no panel answer there is
nothing to fuse and nothing to fall back to, so that is now a declared failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.fusion import FusionConfig, FusionEngine, FusionFailed, receipt_from_trace
from chimera.providers import CompletionResult

FULL = FusionConfig(panel=["m1", "m2", "m3"], judge="judge", synthesizer="synth")
SELECTIVE = FusionConfig(
    panel=["m1", "m2", "m3"], judge="judge", synthesizer="synth", mode="selective", probe_k=2
)
ASK = [{"role": "user", "content": "q"}]


class Stages:
    """A panel with scripted answers, and a judge/synth that can raise or come back empty."""

    def __init__(
        self,
        answers: dict[str, str] | None = None,
        *,
        judge: str | Exception = "JUDGE",
        synth: str | Exception = "FINAL",
        fail: set[str] | None = None,
    ) -> None:
        self.answers = answers or {"m1": "alpha one", "m2": "beta two", "m3": "gamma three"}
        self.judge = judge
        self.synth = synth
        self.fail = fail or set()
        self.calls: list[str | None] = []

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        self.calls.append(model)
        if model in self.fail:
            raise RuntimeError(f"{model} is down")
        if model in ("judge", "synth"):
            out = self.judge if model == "judge" else self.synth
            if isinstance(out, Exception):
                raise out
            return CompletionResult(content=out, model=str(model), finish_reason="stop")
        return CompletionResult(content=self.answers.get(str(model), ""), model=str(model), finish_reason="stop")


def test_a_judge_that_raises_falls_back_to_a_panel_answer() -> None:
    trace = FusionEngine(Stages(judge=RuntimeError("judge 429")), FULL).run(ASK)
    assert trace.final == "alpha one"
    assert trace.aggregation == "fallback"
    assert trace.fallback_stage == "judge"
    assert "judge 429" in trace.fallback_reason
    assert len(trace.successful_panel()) == 3


def test_a_synthesiser_that_raises_falls_back_to_a_panel_answer() -> None:
    trace = FusionEngine(Stages(synth=TimeoutError("synth timed out")), FULL).run(ASK)
    assert trace.final == "alpha one"
    assert trace.aggregation == "fallback"
    assert trace.fallback_stage == "synth"
    assert trace.judge_analysis == "JUDGE"  # the judge's work is still on the record


def test_a_synthesiser_empty_after_its_retry_falls_back_instead_of_shipping_nothing() -> None:
    backend = Stages(synth="")
    trace = FusionEngine(backend, FULL).run(ASK)
    assert backend.calls.count("synth") == 2  # it was asked once more first
    assert trace.final == "alpha one"
    assert trace.aggregation == "fallback"
    assert trace.fallback_stage == "synth"


def test_a_judge_empty_after_its_retry_falls_back_before_the_synthesiser_writes_from_nothing() -> None:
    backend = Stages(judge="")
    trace = FusionEngine(backend, FULL).run(ASK)
    assert "synth" not in backend.calls
    assert trace.aggregation == "fallback"
    assert trace.fallback_stage == "judge"
    assert trace.final == "alpha one"


def test_the_fallback_prefers_the_panel_majority() -> None:
    answers = {"m1": "a lone dissent", "m2": "the answer is 7", "m3": "the answer is 7"}
    trace = FusionEngine(Stages(answers, judge=RuntimeError("down")), FULL).run(ASK)
    assert trace.final == "the answer is 7"


def test_the_fallback_skips_panelists_that_failed_or_came_back_blank() -> None:
    answers = {"m1": "", "m2": "beta two", "m3": "gamma three"}
    trace = FusionEngine(Stages(answers, judge=RuntimeError("down"), fail={"m2"}), FULL).run(ASK)
    assert trace.final == "gamma three"


def test_an_agreed_synthesis_that_raises_falls_back_to_the_probe() -> None:
    answers = {"m1": "the answer is 42", "m2": "the answer is 42"}
    trace = FusionEngine(Stages(answers, synth=RuntimeError("synth 500")), SELECTIVE).run(ASK)
    assert trace.early_stopped is True
    assert trace.final == "the answer is 42"
    assert trace.aggregation == "fallback"
    assert trace.fallback_stage == "synth"


def test_complete_marks_a_fallback_in_route_meta() -> None:
    result = FusionEngine(Stages(judge=RuntimeError("judge 429")), FULL).complete(ASK)
    assert result.content == "alpha one"
    meta = result.route_meta or {}
    assert meta["aggregation"] == "fallback"
    assert meta["fallback_stage"] == "judge"


def test_the_receipt_records_the_fallback_and_the_failing_stage() -> None:
    trace = FusionEngine(Stages(synth=""), FULL).run(ASK)
    receipt = receipt_from_trace(trace).to_json()
    assert receipt["aggregation"] == "fallback"
    assert receipt["fallback_stage"] == "synth"


def test_a_normal_run_is_not_a_fallback() -> None:
    trace = FusionEngine(Stages(), FULL).run(ASK)
    assert trace.aggregation == "synth"
    assert trace.fallback_stage is None
    assert trace.final == "FINAL"


def test_a_panel_that_all_failed_is_a_declared_failure_not_a_fused_answer() -> None:
    backend = Stages(fail={"m1", "m2", "m3"})
    with pytest.raises(FusionFailed) as raised:
        FusionEngine(backend, FULL).run(ASK)
    assert "judge" not in backend.calls and "synth" not in backend.calls
    assert "m1" in str(raised.value) and "is down" in str(raised.value)


def test_a_panel_that_all_came_back_blank_is_a_declared_failure() -> None:
    backend = Stages({"m1": "", "m2": " ", "m3": ""})
    with pytest.raises(FusionFailed):
        FusionEngine(backend, FULL).run(ASK)
    assert "judge" not in backend.calls and "synth" not in backend.calls


def test_selective_fusion_declares_the_failure_too() -> None:
    backend = Stages(fail={"m1", "m2", "m3"})
    with pytest.raises(FusionFailed):
        FusionEngine(backend, SELECTIVE).run(ASK)
    assert "synth" not in backend.calls


def test_the_judge_is_never_handed_an_empty_panel() -> None:
    # The canned "No panel answers were produced." used to reach the synthesiser as the analysis.
    source = (Path(__file__).resolve().parents[1] / "chimera/fusion/engine.py").read_text(encoding="utf-8")
    assert "No panel answers were produced." not in source


def test_the_fuse_command_reports_a_declared_failure_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    import chimera.providers as providers
    from chimera.cli.main import app

    class DownGateway:
        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            raise RuntimeError("every provider is down")

    monkeypatch.setattr(providers, "LLMGateway", DownGateway)
    result = CliRunner().invoke(app, ["fuse", "q", "--full"])
    assert result.exit_code == 1
    assert "no panel model produced an answer" in result.output
    assert not isinstance(result.exception, FusionFailed)  # handled, not raised


class _JudgeDownGateway:
    """Every panelist answers; the judge is down."""

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        from chimera.config import get_settings

        if model == get_settings().fusion_judge:
            raise RuntimeError("judge 429 rate limited")
        return CompletionResult(content=f"panel says hello from {model}", model=str(model), finish_reason="stop")


def _fuse(monkeypatch: pytest.MonkeyPatch, *args: str) -> Any:
    from typer.testing import CliRunner

    import chimera.providers as providers
    from chimera.cli.main import app
    from chimera.config import get_settings

    # `chimera fuse` builds its engine through the factory, whose default cast is the user's ladder
    # with the judge on the top tier, which is also a panelist. A fake that recognises the judge by
    # model name needs a judge that is not on the panel, so the cast is named explicitly, and the
    # factory honours a named cast as it is.
    monkeypatch.setenv("CHIMERA_FUSION_PANEL", "prov/p1,prov/p2,prov/p3")
    monkeypatch.setenv("CHIMERA_FUSION_JUDGE", "prov/judge")
    monkeypatch.setenv("CHIMERA_FUSION_SYNTHESIZER", "prov/synth")
    get_settings.cache_clear()
    monkeypatch.setattr(providers, "LLMGateway", _JudgeDownGateway)
    return CliRunner().invoke(app, ["fuse", "q", "--full", *args], env={"COLUMNS": "200"})


def test_the_fuse_command_says_when_it_shipped_a_panel_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _fuse(monkeypatch)
    assert result.exit_code == 0
    # The engine's own WARNING log line already reached the terminal; what was missing is the answer
    # itself being labelled, so assert on the CLI's sentence, not on the log's.
    assert "this is a panel answer, not a fused one" in result.output
    assert "judge 429 rate limited" in result.output


def test_the_fuse_panel_view_titles_a_fallback_as_a_panel_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _fuse(monkeypatch, "--show-panel")
    assert result.exit_code == 0
    assert "panel answer (aggregation failed)" in result.output
    assert "judge: no analysis" in result.output  # not an empty "judge" box


def test_a_normal_fuse_run_carries_no_fallback_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    import chimera.providers as providers
    from chimera.cli.main import app

    class UpGateway:
        def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
            return CompletionResult(content=f"text from {model}", model=str(model), finish_reason="stop")

    monkeypatch.setattr(providers, "LLMGateway", UpGateway)
    result = CliRunner().invoke(app, ["fuse", "q", "--full", "--show-panel"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "aggregation failed" not in result.output
    assert "not a fused one" not in result.output
