"""An empty or truncated reply never counts as agreement (study 30, S30-01).

Every agreement check in fusion compares normalized text with ``difflib.SequenceMatcher``, and
``SequenceMatcher(None, "", "").ratio()`` is 1.0. So two panelists that both came back empty — the
reasoning model spent its whole completion budget thinking, the provider returned ``content=""``
with ``finish_reason="length"`` — "agreed perfectly". Selective fusion (the default mode) then
skipped the rest of the panel and the judge and asked the synthesiser to write a final answer from
two blank answers. Replaying ``bench/judge_blind_hard/results/collect-all.jsonl`` through the probe
check stopped early on 8 of 50 problems, and every one of the 8 was a pair of empty replies.

The same arithmetic sat in ``consistency.majority`` (two empty samples out of three are a "strict
majority" whose representative is ``""``) and so in the router's agreement escalation and in
self-consistency. A reply cut off at the output ceiling is the same failure with some text in it:
two replies truncated at the same point can be identical without either having answered.
"""

from __future__ import annotations

from typing import Any

from chimera.fusion import FusionConfig, FusionEngine, PanelResponse, RoutedBackend, RoutingPolicy
from chimera.fusion.consistency import SelfConsistency, majority
from chimera.providers import CompletionResult

SELECTIVE = FusionConfig(
    panel=["m1", "m2", "m3"], judge="judge", synthesizer="synth", mode="selective", probe_k=2
)


class Scripted:
    """A fixed reply (and finish_reason) per model; records which models were asked."""

    def __init__(self, answers: dict[str, tuple[str, str]]) -> None:
        self.answers = answers
        self.calls: list[str | None] = []

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        self.calls.append(model)
        if model == "judge":
            return CompletionResult(content="JUDGE", model="judge", finish_reason="stop")
        if model == "synth":
            return CompletionResult(content="FINAL", model="synth", finish_reason="stop")
        content, reason = self.answers.get(str(model), (f"distinct answer from {model}", "stop"))
        return CompletionResult(content=content, model=str(model), finish_reason=reason)


def _engine() -> FusionEngine:
    return FusionEngine(Scripted({}), SELECTIVE)


def test_two_empty_probe_answers_do_not_agree() -> None:
    empty = [PanelResponse(model="a", content=""), PanelResponse(model="b", content="")]
    assert _engine()._agree(empty) is False


def test_two_whitespace_only_probe_answers_do_not_agree() -> None:
    blank = [PanelResponse(model="a", content="  \n"), PanelResponse(model="b", content="\t")]
    assert _engine()._agree(blank) is False


def test_two_identical_truncated_probe_answers_do_not_agree() -> None:
    cut = [
        PanelResponse(model="a", content="Let me think step by", finish_reason="length"),
        PanelResponse(model="b", content="Let me think step by", finish_reason="length"),
    ]
    assert _engine()._agree(cut) is False


def test_two_identical_complete_probe_answers_still_agree() -> None:
    same = [
        PanelResponse(model="a", content="the answer is 42", finish_reason="stop"),
        PanelResponse(model="b", content="The answer is  42", finish_reason=""),
    ]
    assert _engine()._agree(same) is True


def test_selective_fusion_escalates_when_both_probes_came_back_empty() -> None:
    backend = Scripted({"m1": ("", "length"), "m2": ("", "length")})
    trace = FusionEngine(backend, SELECTIVE).run([{"role": "user", "content": "q"}])
    assert trace.early_stopped is False
    assert "m3" in backend.calls and "judge" in backend.calls


def test_selective_fusion_escalates_when_both_probes_were_cut_off_alike() -> None:
    backend = Scripted({"m1": ("Step 1: expand the", "length"), "m2": ("Step 1: expand the", "length")})
    trace = FusionEngine(backend, SELECTIVE).run([{"role": "user", "content": "q"}])
    assert trace.early_stopped is False
    assert "judge" in backend.calls


def test_the_panel_keeps_each_answers_finish_reason() -> None:
    backend = Scripted({"m1": ("x", "length"), "m2": ("y", "stop")})
    trace = FusionEngine(backend, SELECTIVE).run([{"role": "user", "content": "q"}])
    assert [r.finish_reason for r in trace.panel[:2]] == ["length", "stop"]


def test_empty_answers_are_not_a_majority() -> None:
    assert majority(["", "", "the answer is 7"]) is None
    assert majority(["  ", "\n", "", "a", "b"]) is None


def test_a_non_empty_majority_still_wins_beside_empty_answers() -> None:
    assert majority(["the answer is 7", "the answer is 7", ""]) == "the answer is 7"


def test_a_task_typed_vote_never_ships_an_empty_majority() -> None:
    config = FusionConfig(
        panel=["m1", "m2", "m3"], judge="judge", synthesizer="synth", task_typed=True
    )
    backend = Scripted({"m1": ("", "length"), "m2": ("", "length"), "m3": ("x = 4", "stop")})
    trace = FusionEngine(backend, config).run(
        [{"role": "user", "content": "Solve for x: 2x + 3 = 11. What is x?"}]
    )
    assert trace.aggregation != "vote"
    assert trace.final == "FINAL"


class Samples:
    """A single model that returns a scripted sequence of samples; fusion is a separate stub."""

    def __init__(self, samples: list[tuple[str, str]]) -> None:
        self.samples = list(samples)

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        content, reason = self.samples.pop(0)
        return CompletionResult(content=content, model="cheap", finish_reason=reason)


class FusionStub:
    def __init__(self) -> None:
        self.called = False

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.called = True
        return CompletionResult(content="FUSED", model="fusion")


def test_the_router_escalates_when_its_cheap_samples_are_all_empty() -> None:
    fusion = FusionStub()
    router = RoutedBackend(
        Samples([("", "length")] * 3), fusion, RoutingPolicy(mode="never"), agreement_k=3
    )
    result = router.complete([{"role": "user", "content": "q"}])
    assert fusion.called is True
    assert result.content == "FUSED"


def test_the_router_escalates_when_its_cheap_samples_were_cut_off_alike() -> None:
    fusion = FusionStub()
    router = RoutedBackend(
        Samples([("Let me compute", "length")] * 3), fusion, RoutingPolicy(mode="never"), agreement_k=3
    )
    router.complete([{"role": "user", "content": "q"}])
    assert fusion.called is True


def test_the_router_still_takes_a_real_consensus() -> None:
    fusion = FusionStub()
    router = RoutedBackend(
        Samples([("42", "stop"), ("42", "stop"), ("", "length")]),
        fusion,
        RoutingPolicy(mode="never"),
        agreement_k=3,
    )
    result = router.complete([{"role": "user", "content": "q"}])
    assert fusion.called is False
    assert result.content == "42"


def test_self_consistency_does_not_return_an_empty_majority() -> None:
    backend = Samples([("", "length"), ("", "length"), ("7", "stop"), ("RECONCILED", "stop")])
    result = SelfConsistency(backend, n=3).complete([{"role": "user", "content": "q"}])
    assert result.content == "RECONCILED"


def test_self_consistency_does_not_count_truncated_samples_as_votes() -> None:
    backend = Samples(
        [("Step 1", "length"), ("Step 1", "length"), ("7", "stop"), ("RECONCILED", "stop")]
    )
    result = SelfConsistency(backend, n=3).complete([{"role": "user", "content": "q"}])
    assert result.content == "RECONCILED"


class ByTier:
    """A gateway whose weak tier is cut off at the ceiling and whose mid tier answers."""

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        if model == "weak":
            return CompletionResult(content="Step 1: let", model="weak", finish_reason="length")
        return CompletionResult(content="MID", model=str(model), finish_reason="stop")


def test_the_cascade_does_not_accept_a_weak_majority_of_truncated_samples() -> None:
    from chimera.fusion.cascade import CascadeBackend, CascadeConfig

    cascade = CascadeBackend(
        ByTier(), FusionStub(), CascadeConfig(weak="weak", mid="mid", agreement_k=2),
        policy=RoutingPolicy(mode="never"),
    )
    result = cascade.complete([{"role": "user", "content": "q"}])
    assert result.content == "MID"


def _diversity(panel: list[PanelResponse]) -> float | None:
    from chimera.fusion import FusionTrace

    return FusionTrace(panel=panel, judge_analysis="", final="").panel_diversity()


def test_two_blank_answers_are_not_a_converged_panel() -> None:
    # They used to score 0.0, "converged", which is what the desktop badge shows.
    blank = [PanelResponse("m1", "", finish_reason="length"), PanelResponse("m2", " ", finish_reason="stop")]
    assert _diversity(blank) is None


def test_a_blank_beside_an_answer_is_not_maximal_disagreement() -> None:
    # It used to score 1.0; one answer has nothing to be compared with.
    assert _diversity([PanelResponse("m1", "the answer is 7"), PanelResponse("m2", "")]) is None


def test_diversity_is_measured_over_the_answers_only() -> None:
    panel = [
        PanelResponse("m1", "the answer is 7", finish_reason="stop"),
        PanelResponse("m2", "the answer is 7", finish_reason="stop"),
        PanelResponse("m3", "", finish_reason="length"),
    ]
    assert _diversity(panel) == 0.0


def test_self_consistency_with_only_blank_samples_is_a_declared_failure() -> None:
    import pytest

    from chimera.fusion import FusionFailed

    backend = Samples([("", "length"), ("", "stop"), (" ", "length"), ("SYN", "stop")])
    # It used to synthesise from three blanks and ship "SYN" as a consensus of three.
    with pytest.raises(FusionFailed, match="no self-consistency sample produced an answer"):
        SelfConsistency(backend, n=3).complete([{"role": "user", "content": "q"}])
    assert backend.samples == [("SYN", "stop")]  # the synthesiser was never called


def test_self_consistency_with_only_truncated_samples_returns_one_marked_unvoted() -> None:
    backend = Samples([("Step 1", "length"), ("Step 1, then", "length"), ("SYN", "stop")])
    result = SelfConsistency(backend, n=2).complete([{"role": "user", "content": "q"}])
    assert result.content == "Step 1"
    assert result.model == "cheap"  # not "self-consistency": nothing was voted or synthesised
    assert result.finish_reason == "length"
    assert (result.route_meta or {})["aggregation"] == "none"
    assert backend.samples == [("SYN", "stop")]


def test_the_best_of_command_reports_blank_samples_instead_of_a_traceback(monkeypatch: Any) -> None:
    from typer.testing import CliRunner

    import chimera.providers as providers
    from chimera.cli.main import app

    class BlankGateway:
        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            return CompletionResult(content="", model="cheap", finish_reason="length")

    monkeypatch.setattr(providers, "LLMGateway", BlankGateway)
    result = CliRunner().invoke(app, ["fuse", "q", "--best-of", "3"])
    assert result.exit_code == 1
    assert "no self-consistency sample produced an answer" in result.output
