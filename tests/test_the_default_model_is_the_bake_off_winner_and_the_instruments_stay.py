"""The product default moved to gpt-6-luna; the models that MEASURE things did not move with it.

`bench/default-model-bakeoff` (SWE-bench django, official harness, partial and unpaired) resolved
123/154 with gpt-6-luna against 92/150 with deepseek-v4-flash-0731, at under half the cost per
resolved instance. That moves the model a fresh install talks to. It does not move the judge, the
governance and decisions measurements, or the one `useful_k` anyone has measured: those were
calibrated on deepseek, and swapping the model under a calibrated instrument changes the reading
without saying so.
"""

from __future__ import annotations

from chimera.config import _DEFAULT_JUDGE, Settings
from chimera.core.context_budget import ContextBudget, useful_tokens, window_tokens
from chimera.providers.catalog import _PRESETS, PROVIDERS_BY_NAME

LUNA = "openrouter/openai/gpt-6-luna"
DEEPSEEK = "openrouter/deepseek/deepseek-v4-flash-0731"


def _declared(field: str) -> object:
    """The default the CODE ships, not what this process happens to be configured with."""
    info = Settings.model_fields[field]
    if info.default_factory is not None:
        return info.default_factory()  # type: ignore[call-arg]
    return info.default


def test_a_fresh_install_runs_on_gpt_6_luna() -> None:
    assert _declared("default_model") == LUNA


def test_the_openrouter_wizard_suggests_the_model_that_runs() -> None:
    # The wizard shows this without writing it, so a mismatch shows one slug and runs another.
    assert PROVIDERS_BY_NAME["openrouter"].default_model == _declared("default_model")


def test_the_fusion_judge_is_still_the_measured_deepseek() -> None:
    assert _DEFAULT_JUDGE == DEEPSEEK
    assert _declared("fusion_judge") == DEEPSEEK


def test_the_cost_presets_keep_their_rungs() -> None:
    # The bake-off measured the plain agent turn, not the ladder's roles; moving a rung is its own
    # decision.
    assert {mode: ladder.mid for mode, ladder in _PRESETS.items()} == {
        "cheap": DEEPSEEK,
        "balanced": DEEPSEEK,
        "auto": DEEPSEEK,
        "premium": "openrouter/openai/gpt-5.5",
    }


def test_the_new_default_has_no_invented_useful_context() -> None:
    # Nobody has measured what luna still reads well, so compaction falls back to the window
    # fraction for it — the honest behaviour until `bench/useful_context` runs on it.
    assert useful_tokens(LUNA) is None
    assert ContextBudget.for_model(LUNA, fraction=0.6).budget == int(window_tokens(LUNA) * 0.6)
