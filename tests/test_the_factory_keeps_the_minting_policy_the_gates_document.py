"""The default factory keeps the minting policy the gates' own docs describe.

``FailureReproductionGate`` is opt-in by design (``chimera/evolution/reproduction.py``): with
``require_verifier=False`` it returns ``unproven`` for a narration with no executable check, and a
default-on wiring would refuse every anti-pattern card from a run without a verifier — which the
module's own docs say must not happen, because most legitimate small-task runs have none. So the
factory does not build it; the gate stays available to a caller that asks for it.

``HoldoutGate`` is built with no cases, which is wiring, not measurement: it can only record that a
candidate was unmeasured. Its own tests cover what it does with real cases.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from chimera.config import Settings
from chimera.evolution import build_evolution_context
from chimera.evolution.holdout import HoldoutGate
from chimera.providers import CompletionResult

HEX = "0123456789abcdef0123456789abcdef"

ANTI_PATTERN = (
    '{"name": "off_by_one", "description": "fencepost error in loop bounds", '
    '"trigger": "iterating with an index", "do": "iterate 0..n-1", '
    '"avoid": "iterating 0..n", "check": "index never equals the length", '
    '"risk": "empty collections", "triggers": ["loop", "index", "bound"]}'
)


class _Backend:
    def complete(self, messages: list[Any], **_kwargs: Any) -> CompletionResult:
        return CompletionResult(content=ANTI_PATTERN, model="fake")


def _settings(home: Path) -> Settings:
    return Settings(_env_file=None, CHIMERA_HOME=str(home), CHIMERA_SKILL_CARDS=True)  # type: ignore[call-arg]


def test_the_default_factory_does_not_build_the_reproduction_gate(tmp_path: Path) -> None:
    evo = build_evolution_context(_settings(tmp_path), _Backend(), None, home=tmp_path)

    assert evo.auto_evolver is not None
    assert evo.auto_evolver.reproduction is None


def test_the_holdout_is_wired_with_no_cases_so_it_measures_nothing(tmp_path: Path) -> None:
    evo = build_evolution_context(_settings(tmp_path), _Backend(), None, home=tmp_path)

    assert evo.auto_evolver is not None
    assert isinstance(evo.auto_evolver.holdout, HoldoutGate)
    assert evo.auto_evolver.holdout.cases == []


def test_a_narrated_failure_with_no_verifier_still_mints_as_before(tmp_path: Path) -> None:
    """The pre-existing policy, and the reason it stands: a card from narration alone is refused
    only by a caller that asks for the reproduction gate. The default context keeps minting it."""
    evo = build_evolution_context(_settings(tmp_path), _Backend(), None, home=tmp_path)
    assert evo.auto_evolver is not None
    attempts = [SimpleNamespace(verified=False, evidence="none", success=False)]

    card = evo.auto_evolver.maybe_evolve_failure(
        "fix the loop bounds", "the loop ran one past the end", 2,
        attempts=attempts, evidence_id=HEX,
    )

    assert card is not None
    assert "off_by_one" in evo.auto_evolver.store
