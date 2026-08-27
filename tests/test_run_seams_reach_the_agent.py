"""The run seams the CLI has always had and the app could not send.

`repo_map` and `explorer` were declared on the request and defaulted to false with nothing setting
them; spec-grounded test generation and re-plan-on-stall had no field at all.

**The finding that changed the design.** `replan_on_stall` does nothing without a stagnation
detector, and this builder passed none — so turning replan on would have shipped a switch that
changes nothing, which is the exact class of defect the whole roadmap is about. The detector is now
passed unconditionally: it costs no model call, it is what produces the cheap pivot on its own, and
`replan` is what upgrades that pivot into a new plan.

The threshold matters as much as the detector. At the strict default two signatures must be
byte-identical after normalisation, which misses the common case — two attempts failing from one
cause with different assertion text — so the stall would never be detected and the line would be
decoration (`bench/retry_lift`, intervention I2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.providers import CompletionResult


class _Backend:
    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content="ok", model="fake", prompt_tokens=1, completion_tokens=1)


def _agent(tmp_path: Path, **fields: Any) -> Any:
    from chimera.api.app import RunRequest, _build_solve_agent

    req = RunRequest(task="x", workspace=str(tmp_path), **fields)
    return _build_solve_agent(
        req, tmp_path, lambda _e: None, Settings(CHIMERA_HOME=str(tmp_path / "h"))
    )


@pytest.fixture(autouse=True)
def _no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.providers.LLMGateway", _Backend)


def test_the_stall_detector_is_always_there(tmp_path: Path) -> None:
    """Free — a comparison of successive failure signatures, no model call. Without it every
    attempt that failed the same way as the last got the same nudge, and the loop refined a dead
    end for its whole attempt budget."""
    assert _agent(tmp_path).stagnation is not None


def test_the_detector_matches_approximately(tmp_path: Path) -> None:
    """The threshold is the point, not a tuning choice. Byte-identical matching misses two attempts
    failing from one cause with different assertion text, so a strict detector would be a detector
    that never fires."""
    assert _agent(tmp_path).stagnation.signature_similarity < 1.0


def test_replan_is_off_unless_asked(tmp_path: Path) -> None:
    """It spends a planning call at the worst moment."""
    assert _agent(tmp_path).replan_on_stall is False
    assert _agent(tmp_path, replan=True).replan_on_stall is True


def test_replan_has_something_to_replan_with(tmp_path: Path) -> None:
    """`replan_on_stall` reads `self.planner` and `config.use_planner` before it does anything. A
    run with replan on and no planner would report a setting that cannot fire."""
    agent = _agent(tmp_path, replan=True)
    assert agent.planner is not None
    assert agent.config.use_planner is True


def test_test_generation_is_off_unless_asked(tmp_path: Path) -> None:
    assert _agent(tmp_path).spec_test_generator is None
    assert _agent(tmp_path, gen_tests=True).spec_test_generator is not None


def test_the_repository_seams_arrive(tmp_path: Path) -> None:
    """Declared on the request, defaulted to false, and set by nothing until now."""
    assert _agent(tmp_path).repo_map is False
    assert _agent(tmp_path, repo_map=True).repo_map is True


def test_generation_only_replaces_the_weaker_gate(tmp_path: Path) -> None:
    """The loop uses the generator only when there is no verifier and the requirements are not
    empty. Asserted here so the screen's decision not to offer it elsewhere is backed by the
    behaviour rather than by the screen agreeing with itself."""
    import inspect

    from chimera.core.autonomous import AutonomousAgent

    source = inspect.getsource(AutonomousAgent.run)
    assert "self.spec_test_generator is not None and self.verifier is None and requirements" in source
