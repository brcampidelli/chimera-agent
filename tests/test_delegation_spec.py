"""Tests for the M16-A1 foundation: delegation contract + tier ladder + catalog."""

from __future__ import annotations

import pytest

from chimera.config import Settings
from chimera.orchestration.spec import (
    SUMMARY_MAX_CHARS,
    EffortBudget,
    ResultEnvelope,
    TaskSpec,
    validate_envelope,
)
from chimera.providers.catalog import (
    COST_MODES,
    entries,
    register_catalog_prices,
    resolve_tiers,
)

# ---------------------------------------------------------------------------
# TaskSpec / ResultEnvelope contract
# ---------------------------------------------------------------------------


def _spec(**overrides: object) -> TaskSpec:
    base: dict[str, object] = {
        "task_id": "t1",
        "objective": "Summarize the release notes",
        "output_format": "3 bullet points",
        "boundaries": "Do not modify files",
        "allowed_tools": ["read_file"],
        "context": "notes.md contains the notes",
    }
    base.update(overrides)
    return TaskSpec(**base)  # type: ignore[arg-type]


def test_spec_round_trips_through_json() -> None:
    spec = _spec(effort=EffortBudget(max_tokens=1234, max_steps=3))
    again = TaskSpec.model_validate_json(spec.model_dump_json())
    assert again == spec
    assert again.effort.max_tokens == 1234


def test_spec_render_contains_contract_sections() -> None:
    text = _spec().render()
    assert "## Objective" in text
    assert "## Boundaries (do not exceed)" in text
    assert "## Result contract" in text
    assert "read_file" in text


def test_envelope_round_trips_through_json() -> None:
    env = ResultEnvelope(
        task_id="t1", summary="done", evidence_refs=["runs/x/out.txt"], gaps=["no source B"]
    )
    again = ResultEnvelope.model_validate_json(env.model_dump_json())
    assert again == env


def test_validate_envelope_accepts_good_envelope() -> None:
    env = ResultEnvelope(task_id="t1", status="ok", summary="All three bullets ...")
    assert validate_envelope(_spec(), env) == []


def test_validate_envelope_flags_task_id_mismatch() -> None:
    env = ResultEnvelope(task_id="other", summary="x")
    problems = validate_envelope(_spec(), env)
    assert any("task_id mismatch" in p for p in problems)


def test_validate_envelope_flags_empty_ok_summary() -> None:
    env = ResultEnvelope(task_id="t1", status="ok", summary="   ")
    problems = validate_envelope(_spec(), env)
    assert any("summary is empty" in p for p in problems)


def test_validate_envelope_enforces_summary_cap() -> None:
    env = ResultEnvelope(task_id="t1", summary="x" * (SUMMARY_MAX_CHARS + 1))
    problems = validate_envelope(_spec(), env)
    assert any("exceeds cap" in p for p in problems)


def test_validate_envelope_requires_explanation_on_failure() -> None:
    env = ResultEnvelope(task_id="t1", status="failed", summary="", gaps=[])
    problems = validate_envelope(_spec(), env)
    assert any("no explanation" in p for p in problems)
    # A failure WITH gaps is acceptable.
    env2 = ResultEnvelope(task_id="t1", status="failed", gaps=["rate-limited"])
    assert validate_envelope(_spec(), env2) == []


def test_validate_envelope_checks_output_schema() -> None:
    spec = _spec(output_schema={"type": "object", "required": ["title", "score"]})
    bad = ResultEnvelope(task_id="t1", summary="not json at all")
    assert any("not valid JSON" in p for p in validate_envelope(spec, bad))
    missing = ResultEnvelope(task_id="t1", summary='{"title": "x"}')
    assert any("missing required keys: score" in p for p in validate_envelope(spec, missing))
    good = ResultEnvelope(task_id="t1", summary='{"title": "x", "score": 3}')
    assert validate_envelope(spec, good) == []
    fenced = ResultEnvelope(task_id="t1", summary='```json\n{"title": "x", "score": 3}\n```')
    assert validate_envelope(spec, fenced) == []


# ---------------------------------------------------------------------------
# Tier ladder resolution (explicit override > cost_mode > default)
# ---------------------------------------------------------------------------


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_tier_fields_default_unpinned(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CHIMERA_WEAK_MODEL", "CHIMERA_MID_MODEL", "CHIMERA_ORCHESTRATOR_MODEL",
                "CHIMERA_COST_MODE", "CHIMERA_CASCADE"):
        monkeypatch.delenv(var, raising=False)
    settings = _settings()
    assert settings.weak_model == ""
    assert settings.mid_model == ""
    assert settings.orchestrator_model == ""
    assert settings.cost_mode == "auto"
    assert settings.cascade is False
    assert settings.delegation_budget == 8000


def test_auto_mode_enters_at_mid(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CHIMERA_WEAK_MODEL", "CHIMERA_MID_MODEL", "CHIMERA_ORCHESTRATOR_MODEL",
                "CHIMERA_COST_MODE"):
        monkeypatch.delenv(var, raising=False)
    ladder = _settings().tier_ladder()
    assert ladder.entry == "mid"  # "automático prioriza o médio"
    assert ladder.weak and ladder.mid and ladder.top
    assert ladder.ladder() == [ladder.weak, ladder.mid, ladder.top]


def test_explicit_override_beats_cost_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_COST_MODE", "cheap")
    monkeypatch.setenv("CHIMERA_ORCHESTRATOR_MODEL", "openrouter/moonshotai/kimi-k2")
    monkeypatch.delenv("CHIMERA_WEAK_MODEL", raising=False)
    monkeypatch.delenv("CHIMERA_MID_MODEL", raising=False)
    ladder = _settings().tier_ladder()
    assert ladder.top == "openrouter/moonshotai/kimi-k2"  # the pin wins
    assert ladder.entry == "weak"  # mode still shapes the unpinned parts


def test_cheap_mode_never_pays_reasoner_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CHIMERA_WEAK_MODEL", "CHIMERA_MID_MODEL", "CHIMERA_ORCHESTRATOR_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CHIMERA_COST_MODE", "cheap")
    ladder = _settings().tier_ladder()
    assert ladder.top == ladder.mid  # top collapses onto the mid workhorse


def test_unknown_cost_mode_falls_back_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CHIMERA_WEAK_MODEL", "CHIMERA_MID_MODEL", "CHIMERA_ORCHESTRATOR_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CHIMERA_COST_MODE", "turbo-max")
    ladder = _settings().tier_ladder()
    assert ladder.entry == "mid"  # auto semantics


def test_resolve_tiers_covers_all_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CHIMERA_WEAK_MODEL", "CHIMERA_MID_MODEL", "CHIMERA_ORCHESTRATOR_MODEL"):
        monkeypatch.delenv(var, raising=False)
    for mode in COST_MODES:
        monkeypatch.setenv("CHIMERA_COST_MODE", mode)
        ladder = resolve_tiers(_settings())
        assert ladder.weak and ladder.mid and ladder.top, mode


# ---------------------------------------------------------------------------
# Catalog data + price registration
# ---------------------------------------------------------------------------


def test_catalog_has_multiple_vendors_per_tier() -> None:
    for tier in ("weak", "mid", "top"):
        vendors = {e.vendor for e in entries(tier=tier)}  # type: ignore[arg-type]
        assert len(vendors) >= 2, f"tier {tier} must not be single-vendor"


def test_catalog_vendor_filter() -> None:
    deepseek = entries(vendor="deepseek")
    assert deepseek and all("deepseek" in e.vendor.lower() for e in deepseek)


def test_register_catalog_prices_makes_free_tier_zero() -> None:
    from chimera.fusion.receipts import resolve_price

    register_catalog_prices()
    price = resolve_price("openrouter/qwen/qwen3-next-80b-a3b-instruct:free")
    assert price is not None
    assert price.input_per_m == 0.0 and price.output_per_m == 0.0
