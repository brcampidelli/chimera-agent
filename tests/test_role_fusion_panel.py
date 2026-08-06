"""A fused role convenes the USER'S ladder — not three frontier models nobody asked for.

This is a regression test for a bug that cost real money and was invisible from every surface. A
profile said `plan = <top tier>`; the code built `FusionEngine(gateway)` with no config; the engine
fell back to `FusionConfig.from_settings()`, whose default panel is Opus + GPT-5.5 + Gemini. So a
profile chosen under a *cheap* cost mode silently billed frontier rates. Nothing reported it: a bare
engine writes no route log, the role's model was dropped without a word, and the only symptom was a
benchmark arm that ran inexplicably slowly.

Two of these tests would have caught it before a dollar was spent.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from chimera.api.roles import fusion_for_role, resolve
from chimera.config import Settings
from chimera.providers.catalog import resolve_tiers


def _settings(**over: Any) -> Settings:
    return Settings(CHIMERA_HOME="/tmp/chimera-role-fusion", **over)


def test_the_panel_is_the_users_ladder_not_the_frontier_default() -> None:
    """The bug, stated directly. Under a cheap cost mode, the panel must be the cheap models."""
    settings = _settings(CHIMERA_COST_MODE="cheap")
    ladder = resolve_tiers(settings)  # type: ignore[arg-type]

    panel = fusion_for_role(object(), settings).config.panel  # type: ignore[attr-defined]

    assert set(panel) <= {ladder.weak, ladder.mid, ladder.top}
    # The exact models that were silently billed before the fix.
    assert not {
        "openrouter/anthropic/claude-opus-4-8",
        "openrouter/openai/gpt-5.5",
        "openrouter/google/gemini-3.1-pro",
    } & set(panel)


def test_the_judge_and_synthesizer_are_the_top_tier() -> None:
    settings = _settings()
    ladder = resolve_tiers(settings)  # type: ignore[arg-type]
    config = fusion_for_role(object(), settings).config  # type: ignore[attr-defined]

    assert config.judge == ladder.top and config.synthesizer == ladder.top


def test_a_cost_mode_that_collapses_two_tiers_does_not_ask_one_model_twice() -> None:
    """`cheap` points mid and top at the same model. A panel that asks it the same question twice
    pays twice for one opinion and then treats the agreement as a signal."""
    settings = _settings(CHIMERA_COST_MODE="cheap")
    panel = fusion_for_role(object(), settings).config.panel  # type: ignore[attr-defined]

    assert len(panel) == len(set(panel))


def test_an_explicit_panel_still_wins() -> None:
    """Someone who named a panel meant it. The check is `model_fields_set` — "explicitly provided"
    rather than "happens to equal the default", which is the distinction that hid the bug."""
    settings = _settings(CHIMERA_FUSION_PANEL="vendor/a,vendor/b")
    panel = fusion_for_role(object(), settings).config.panel  # type: ignore[attr-defined]

    assert panel == ["vendor/a", "vendor/b"]


def test_behavioural_settings_are_preserved_only_the_models_change() -> None:
    """Mode, probe_k and thresholds are behaviour the user tuned; none of them changes which models
    get billed, so none of them is ours to override."""
    settings = _settings(CHIMERA_FUSION_MODE="selective", CHIMERA_FUSION_PROBE_K="3")
    config = fusion_for_role(object(), settings).config  # type: ignore[attr-defined]

    assert config.mode == "selective" and config.probe_k == 3


def test_the_engine_warns_loudly_when_a_caller_passes_a_model(caplog: Any) -> None:
    """A panel has no single model to honour — but a caller who passes one BELIEVES they chose it,
    and until this warning existed they were wrong in silence. That silence is what cost money."""
    from chimera.fusion.engine import FusionConfig, FusionEngine
    from chimera.providers import CompletionResult

    class _Backend:
        def complete(self, messages: list[Any], **_: object) -> CompletionResult:
            return CompletionResult(content="x", model="vendor/panelist")

    engine = FusionEngine(
        _Backend(), FusionConfig(panel=["vendor/a"], judge="vendor/j", synthesizer="vendor/s")
    )
    with caplog.at_level(logging.WARNING):
        engine.complete([{"role": "user", "content": "hi"}], model="vendor/i-thought-i-picked-this")

    assert any("fusion ignores model=" in r.getMessage() for r in caplog.records)


def test_no_warning_when_no_model_was_passed(caplog: Any) -> None:
    """The warning has to stay rare enough to be read."""
    from chimera.fusion.engine import FusionConfig, FusionEngine
    from chimera.providers import CompletionResult

    class _Backend:
        def complete(self, messages: list[Any], **_: object) -> CompletionResult:
            return CompletionResult(content="x", model="vendor/panelist")

    engine = FusionEngine(
        _Backend(), FusionConfig(panel=["vendor/a"], judge="vendor/j", synthesizer="vendor/s")
    )
    with caplog.at_level(logging.WARNING):
        engine.complete([{"role": "user", "content": "hi"}])

    assert not [r for r in caplog.records if "fusion ignores model=" in r.getMessage()]


@pytest.mark.parametrize("profile", ["balanced", "max"])
def test_a_fusing_profile_reaches_the_solve_agent_with_the_ladder_panel(
    profile: str, tmp_path: Any
) -> None:
    """Asserted through the real builder, because the bug was never in the helper — it was in what
    the three call sites did with it."""
    from chimera.api.app import RunRequest, _build_solve_agent
    from chimera.fusion.engine import FusionEngine

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    ladder = resolve_tiers(settings)  # type: ignore[arg-type]

    agent = _build_solve_agent(RunRequest(task="t", profile=profile), ws, lambda _e: None, settings)

    if resolve(profile, settings).models.fuse_plan:  # type: ignore[arg-type]
        backend = agent.planner.backend
        assert isinstance(backend, FusionEngine)
        assert set(backend.config.panel) <= {ladder.weak, ladder.mid, ladder.top}
