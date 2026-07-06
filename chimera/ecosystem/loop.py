"""RFT — a rejection-sampling fine-tuning loop, gated by an honest A/B bench.

This closes the self-improvement spiral at the *model* level, honestly. The mechanism is classic
rejection-sampling fine-tuning (a.k.a. RFT / STaR): keep only the runs that actually succeeded with
a high reward and a clean process as training targets, discard the rest. But collecting a good
dataset is not proof the round *helps* — training on it can regress. So the round is **gated by the
same A/B engine every other M14 feature reports against**: a candidate policy is promoted only when
it beats the baseline on a held-out bench with a confidence interval that excludes zero. No lift,
no promotion — you do not train on noise, and you do not ship a round you cannot measure.

Chimera does not train weights in-process — that stays external and opt-in (see
``chimera.ecosystem.evolution``). This loop owns the *decision*: rejection-sample, check there is
enough signal, run the A/B gate, and only then emit the training dataset + recipe. The bench
evaluator is injected, so the whole loop is testable without a network or a GPU.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from chimera.ecosystem.evolution import CurationConfig, curate_sft, write_jsonl, write_recipe
from chimera.ecosystem.trajectory import Trajectory, TrajectoryCollector
from chimera.eval.bench_ab import ABResult, compare
from chimera.telemetry import get_logger

_log = get_logger("ecosystem.loop")


@dataclass
class RejectionResult:
    """The outcome of rejection sampling: which runs were accepted as training targets."""

    accepted: list[Trajectory] = field(default_factory=list)
    total: int = 0
    per_prompt: dict[str, int] = field(default_factory=dict)

    @property
    def accept_rate(self) -> float:
        """Fraction of runs that cleared the bar — a low rate flags a too-hard task or bad reward."""
        return len(self.accepted) / self.total if self.total else 0.0


def rejection_sample(
    trajectories: list[Trajectory],
    *,
    min_reward: float = 0.5,
    min_process: float = 0.0,
    top_k_per_prompt: int = 0,
) -> RejectionResult:
    """Accept only successful, high-reward, clean-process runs (top-k per prompt when set).

    This is the rejection step of RFT: a run is a training target only if it *succeeded* with
    reward at or above the bar and a process score at or above the filter (no lucky-but-sloppy
    successes). ``top_k_per_prompt`` keeps only the best few per unique task, so an easy prompt
    with many passes does not swamp the dataset.
    """
    per_prompt_count: dict[str, int] = {}
    accepted: list[Trajectory] = []
    for item in sorted(trajectories, key=lambda t: -t.reward):
        if item.outcome != "success" or item.reward < min_reward or item.process_score() < min_process:
            continue
        prompt = item.prompt.strip()
        if top_k_per_prompt and per_prompt_count.get(prompt, 0) >= top_k_per_prompt:
            continue
        per_prompt_count[prompt] = per_prompt_count.get(prompt, 0) + 1
        accepted.append(item)
    return RejectionResult(accepted=accepted, total=len(trajectories), per_prompt=per_prompt_count)


@dataclass
class RFTRound:
    """One round's verdict: what was accepted, whether the bench gate passed, and why."""

    accepted_examples: int
    accept_rate: float
    ready: bool
    promoted: bool
    reason: str
    ab: ABResult | None = None
    accepted: list[Trajectory] = field(default_factory=list, repr=False)

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {
            "accepted_examples": self.accepted_examples,
            "accept_rate": round(self.accept_rate, 4),
            "ready": self.ready,
            "promoted": self.promoted,
            "reason": self.reason,
        }
        if self.ab is not None:
            out["ab"] = self.ab.summary()
        return out


class BenchEvaluator(Protocol):
    """Returns the per-trial pass/fail list for an arm ("baseline" or "candidate") on the bench."""

    def evaluate(self, arm: str) -> list[bool]: ...


class StaticEvaluator:
    """A BenchEvaluator backed by pre-computed results (e.g. two terminal-bench runs)."""

    def __init__(self, baseline: list[bool], candidate: list[bool]) -> None:
        self._arms = {"baseline": list(baseline), "candidate": list(candidate)}

    def evaluate(self, arm: str) -> list[bool]:
        return list(self._arms.get(arm, []))


class RejectionSamplingLoop:
    """Rejection-sample trajectories, then promote the round only if it wins the A/B bench gate."""

    def __init__(
        self,
        collector: TrajectoryCollector,
        evaluator: BenchEvaluator,
        *,
        min_reward: float = 0.5,
        min_examples: int = 30,
        min_process: float = 0.0,
        top_k_per_prompt: int = 0,
    ) -> None:
        self.collector = collector
        self.evaluator = evaluator
        self.min_reward = min_reward
        self.min_examples = min_examples
        self.min_process = min_process
        self.top_k_per_prompt = top_k_per_prompt

    def run(self) -> RFTRound:
        """Rejection-sample, check readiness, then apply the A/B bench gate. Never trains."""
        rs = rejection_sample(
            self.collector.all(),
            min_reward=self.min_reward,
            min_process=self.min_process,
            top_k_per_prompt=self.top_k_per_prompt,
        )
        n = len(rs.accepted)
        if n < self.min_examples:
            return RFTRound(
                accepted_examples=n,
                accept_rate=rs.accept_rate,
                ready=False,
                promoted=False,
                reason=f"insufficient accepted examples ({n}/{self.min_examples}) — keep collecting",
                accepted=rs.accepted,
            )
        ab = compare(
            self.evaluator.evaluate("baseline"),
            self.evaluator.evaluate("candidate"),
            baseline_name="baseline",
            treatment_name="candidate",
        )
        promoted = ab.significant
        reason = (
            "candidate beats baseline (CI excludes 0) — promote this round"
            if promoted
            else "no significant lift on the bench — round withheld (don't train on noise)"
        )
        _log.debug("rft round: %d accepted, promoted=%s (%s)", n, promoted, reason)
        return RFTRound(
            accepted_examples=n,
            accept_rate=rs.accept_rate,
            ready=True,
            promoted=promoted,
            reason=reason,
            ab=ab,
            accepted=rs.accepted,
        )

    def export(
        self,
        round_result: RFTRound,
        out_dir: Path,
        *,
        base_model: str = "meta-llama/Llama-3.1-8B-Instruct",
        force: bool = False,
    ) -> list[Path]:
        """Write the accepted dataset + a LoRA recipe — only for a promoted round (unless ``force``).

        Withholding the artifacts of an unpromoted round is the point: a round that did not beat the
        bench should not quietly leave a trainable dataset lying around to be run by mistake.
        """
        if not round_result.promoted and not force:
            return []
        out_dir = Path(out_dir)
        rows = curate_sft(round_result.accepted, CurationConfig(min_reward=self.min_reward))
        dataset = out_dir / "dataset.jsonl"
        write_jsonl(dataset, rows)
        recipe = write_recipe(out_dir, base_model=base_model, fmt="sft", dataset="dataset.jsonl")
        return [dataset, *recipe]


def run_rft(
    collector: TrajectoryCollector,
    baseline_passed: list[bool],
    candidate_passed: list[bool],
    *,
    min_reward: float = 0.5,
    min_examples: int = 30,
    min_process: float = 0.0,
    top_k_per_prompt: int = 0,
) -> RFTRound:
    """Convenience: run one RFT round against two pre-computed bench result lists."""
    loop = RejectionSamplingLoop(
        collector,
        StaticEvaluator(baseline_passed, candidate_passed),
        min_reward=min_reward,
        min_examples=min_examples,
        min_process=min_process,
        top_k_per_prompt=top_k_per_prompt,
    )
    return loop.run()
