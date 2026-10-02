"""The failure-reproduction gate: a card must cite a failure that actually happened.

Phantom Guardrails (arXiv 2607.13083) showed self-improving harnesses distilling fixes for
failures that never happened. This repo measured the same shape at home before the gate: a
Manager rejected an attempt whose receipt's own diff proved the work was done, and the
hallucination outlived the run as a permanent anti-pattern card. These tests pin the three
verdicts and the rule that a refused narrative never becomes a card.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from chimera.evolution import AutoSkillEvolver, SkillEvolver, SkillStore
from chimera.evolution.reproduction import FailureReproductionGate
from chimera.governance.validator import SkillValidator
from chimera.providers import CompletionResult

ANTI_PATTERN = (
    '{"name": "off_by_one", "description": "fencepost error in loop bounds", '
    '"trigger": "iterating with an index", "do": "iterate 0..n-1", '
    '"avoid": "iterating 0..n", "check": "index never equals the length", '
    '"risk": "empty collections", "triggers": ["loop", "index", "bound"]}'
)


class ScriptedBackend:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        content = self._contents.pop(0) if self._contents else ""
        return CompletionResult(content=content, model="fake")


def _auto(
    store: SkillStore,
    *,
    reproduction: FailureReproductionGate | None = None,
    validator: Any = None,
) -> AutoSkillEvolver:
    return AutoSkillEvolver(
        SkillEvolver(ScriptedBackend([ANTI_PATTERN])),
        store,
        validator=validator if validator is not None else SkillValidator(),
        reproduction=reproduction,
    )


def _attempt(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {"verified": False, "evidence": "none", "success": False}
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_verifier_failed_attempt_reproduces_the_failure(tmp_path: Path) -> None:
    """A command that exited non-zero is ground truth: the card is minted."""
    store = SkillStore(tmp_path / "s.json")
    gate = FailureReproductionGate()
    auto = _auto(store, reproduction=gate)
    attempts = [_attempt(verified=False, evidence="verifier")]
    verdict = gate.evaluate("it failed", attempts)
    assert verdict.reproduced and verdict.verifier_failed == 1
    card = auto.maybe_evolve_failure("t", "it failed", 2, attempts=attempts)
    assert card is not None and "off_by_one" in store


def test_narration_alone_is_unproven_and_refused(tmp_path: Path) -> None:
    """No verifier spoke, nothing changed on disk: the narrative is the only evidence."""
    store = SkillStore(tmp_path / "s.json")
    gate = FailureReproductionGate()
    auto = _auto(store, reproduction=gate)
    attempts = [_attempt(verified=False, evidence="none")]
    verdict = gate.evaluate("the model said the loop was wrong", attempts)
    assert verdict.outcome == "unproven" and not verdict.reproduced
    assert auto.maybe_evolve_failure("t", "narrative", 2, attempts=attempts) is None
    assert len(store) == 0


def test_reviewer_overriding_a_passing_verifier_is_contradicted(tmp_path: Path) -> None:
    """The paper's case, named: the evidence shows the cited failure never happened."""
    store = SkillStore(tmp_path / "s.json")
    gate = FailureReproductionGate()
    auto = _auto(store, reproduction=gate)
    attempts = [_attempt(verified=True, evidence="verifier", success=False)]
    verdict = gate.evaluate("the reviewer said the work was missing", attempts)
    assert verdict.outcome == "contradicted" and not verdict.reproduced
    assert auto.maybe_evolve_failure("t", "reviewer override", 2, attempts=attempts) is None
    assert len(store) == 0


def test_no_attempts_at_all_is_unproven_never_a_pass(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    gate = FailureReproductionGate()
    auto = _auto(store, reproduction=gate)
    verdict = gate.evaluate("narrative", None)
    assert verdict.outcome == "unproven" and not verdict.reproduced
    assert auto.maybe_evolve_failure("t", "narrative", 2) is None
    assert len(store) == 0


def test_require_verifier_hardens_unproven_into_contradicted(tmp_path: Path) -> None:
    gate = FailureReproductionGate(require_verifier=True)
    attempts = [_attempt(verified=False, evidence="none")]
    assert gate.evaluate("narrative", attempts).outcome == "contradicted"


def test_without_the_gate_behaviour_is_unchanged(tmp_path: Path) -> None:
    """Opt-in means off changes nothing: a narrative still mints a card, as before."""
    store = SkillStore(tmp_path / "s.json")
    auto = _auto(store, reproduction=None)
    assert auto.maybe_evolve_failure("t", "narrative", 2) is not None
    assert "off_by_one" in store


def test_every_verdict_is_audited(tmp_path: Path) -> None:
    """A gate whose refusal rate is zero supports nothing — so the refusals are recorded."""
    from chimera.governance.audit import AuditLog

    store = SkillStore(tmp_path / "s.json")
    audit = AuditLog(tmp_path / "audit.jsonl")
    auto = _auto(store, reproduction=FailureReproductionGate())
    auto.audit = audit
    attempts = [_attempt(verified=False, evidence="none")]
    auto.maybe_evolve_failure("t", "narrative", 2, attempts=attempts)
    events = [e for e in audit.entries() if e.get("type") == "failure_reproduction"]
    assert len(events) == 1
    assert events[0]["outcome"] == "unproven"
