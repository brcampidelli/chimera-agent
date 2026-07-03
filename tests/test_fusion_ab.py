"""Tests for the fusion A/B bench (no network)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from chimera.eval.continuous import EvalTask
from chimera.eval.fusion_ab import ABReport, ABRow, run_fusion_ab
from chimera.fusion import FusionConfig
from chimera.providers import CompletionResult

FULL = FusionConfig(panel=["m1", "m2", "m3"], judge="judge", synthesizer="synth")
SELECTIVE = replace(FULL, mode="selective", probe_k=2)


class ABBackend:
    """Panel answers come from a per-model map; judge/synth are fixed, tokens reported."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        if model == "judge":
            return CompletionResult(content="JUDGE", model="judge", prompt_tokens=10, completion_tokens=5)
        if model == "synth":
            return CompletionResult(content="FINAL", model="synth", prompt_tokens=8, completion_tokens=4)
        return CompletionResult(
            content=self.answers.get(str(model), f"ans:{model}"),
            model=str(model),
            prompt_tokens=6,
            completion_tokens=3,
        )


def test_ab_runs_both_modes_and_scores() -> None:
    # Probe models agree -> selective early-stops and is cheaper, both pass.
    backend = ABBackend({"m1": "42", "m2": "42", "m3": "42"})
    task = EvalTask("t", "q", lambda out: out == "FINAL")
    report = run_fusion_ab(backend, [task], full_config=FULL, selective_config=SELECTIVE)
    assert isinstance(report, ABReport)
    row = report.rows[0]
    assert isinstance(row, ABRow)
    assert row.full_ok and row.selective_ok
    assert row.early_stopped is True
    # full = 3 panel + judge + synth; selective (early-stop) = 2 panel + synth -> fewer tokens.
    assert row.selective_tokens is not None and row.full_tokens is not None
    assert row.selective_tokens < row.full_tokens


def test_ab_summary_metrics() -> None:
    agree = EvalTask("agree", "q1", lambda out: out == "FINAL")
    disagree_backend_task = EvalTask("dis", "q2", lambda out: out == "FINAL")
    # Both tasks pass here; one early-stops, one escalates (different probe answers).
    report = run_fusion_ab(
        ABBackend({"m1": "same", "m2": "same", "m3": "same"}),
        [agree],
        full_config=FULL,
        selective_config=SELECTIVE,
    )
    escalate = run_fusion_ab(
        ABBackend({"m1": "alpha beta gamma", "m2": "totally unrelated wording", "m3": "x"}),
        [disagree_backend_task],
        full_config=FULL,
        selective_config=SELECTIVE,
    )
    s1 = report.summary()
    assert s1["full_accuracy"] == 1.0 and s1["selective_accuracy"] == 1.0
    assert s1["pct_early_stopped"] == 100.0
    assert s1["token_reduction_pct"] > 0  # selective was cheaper
    s2 = escalate.summary()
    assert s2["pct_early_stopped"] == 0.0
    # Escalated selective run costs the same as full -> no token reduction.
    assert s2["token_reduction_pct"] == 0.0


def test_ab_empty_suite() -> None:
    report = run_fusion_ab(ABBackend({}), [], full_config=FULL, selective_config=SELECTIVE)
    assert report.summary() == {"tasks": 0.0}
