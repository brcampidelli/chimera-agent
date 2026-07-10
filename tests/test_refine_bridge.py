"""M19-A5: GEPA refine bridge — mine verified trajectories, refine, transfer-gate. Offline."""

from __future__ import annotations

from chimera.ecosystem.trajectory import Trajectory
from chimera.evolution.gepa import Scorer, TaskInstance
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.refine_bridge import (
    instances_from_trajectories,
    refine_skill,
)

GOOD = "improved-template"
SEED = "seed-template"


def _yes_scorer() -> Scorer:
    return lambda out: 1.0 if out == "yes" else 0.0


def _no_scorer() -> Scorer:
    return lambda out: 1.0 if out == "no" else 0.0


class FakeExec:
    """Returns 'yes' only for the improved template, 'no' otherwise."""

    def run(self, template: str, task_input: dict[str, str]) -> str:
        return "yes" if template == GOOD else "no"


class FakeReflect:
    """Always proposes the improved template."""

    def propose(self, template: str, feedback: str) -> str:
        return GOOD


def _skill() -> LearnedSkill:
    return LearnedSkill(name="s", description="d", prompt_template=SEED, version="0.1.0")


def _instances(n: int, scorer: Scorer) -> list[TaskInstance]:
    return [TaskInstance(input={"task": f"t{i}"}, scorer=scorer) for i in range(n)]


# --- mining ---------------------------------------------------------------


def test_instances_only_from_verified_productive_trajectories() -> None:
    trajs = [
        Trajectory(seq=0, prompt="p1", response="ok", outcome="success", reward=1.0),
        Trajectory(seq=1, prompt="p2", response="x", outcome="failure", reward=0.0),
        Trajectory(seq=2, prompt="p3", response="x", outcome="success", reward=0.5),  # below min_reward
        Trajectory(seq=3, prompt="p4", response="x", outcome="success", reward=1.0, diff_productive=False),
        Trajectory(seq=4, prompt="p5", response="ok", outcome="success", reward=1.0, diff_productive=None),
    ]
    instances = instances_from_trajectories(trajs, min_reward=1.0)
    assert len(instances) == 2  # only p1 and p5 (verified, at reward, not a hollow diff)


# --- transfer-gated refine ------------------------------------------------


def test_refine_promotes_on_non_regressing_transfer() -> None:
    outcome = refine_skill(
        _skill(), _instances(4, _yes_scorer()),
        executor=FakeExec(), reflector=FakeReflect(),
        holdout=_instances(2, _yes_scorer()),
    )
    assert outcome.result.improved
    assert outcome.decision.transfer_measured
    assert outcome.promoted
    assert outcome.skill.prompt_template == GOOD
    assert outcome.skill.version == "0.1.1"  # version bumped on adoption


def test_refine_without_holdout_is_dry_run() -> None:
    outcome = refine_skill(
        _skill(), _instances(4, _yes_scorer()),
        executor=FakeExec(), reflector=FakeReflect(),
        holdout=None,
    )
    assert outcome.result.improved
    assert outcome.decision.promote  # helped the tuned slice...
    assert not outcome.decision.transfer_measured  # ...but transfer was not measured
    assert not outcome.promoted  # so it is NOT adopted (dry-run)
    assert outcome.skill.prompt_template == SEED  # unchanged


def test_refine_blocks_on_negative_transfer() -> None:
    # The refined template helps the tuned slice but REGRESSES the same-capability holdout.
    outcome = refine_skill(
        _skill(), _instances(4, _yes_scorer()),
        executor=FakeExec(), reflector=FakeReflect(),
        holdout=_instances(2, _no_scorer()),  # here 'no' (the seed's output) is what passes
    )
    assert outcome.result.improved
    assert outcome.decision.transfer_measured
    assert not outcome.decision.promote  # negative transfer caught
    assert not outcome.promoted
    assert outcome.skill.prompt_template == SEED
