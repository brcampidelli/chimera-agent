"""``via`` is one technical sentence; ``via_kind`` is the category a screen can translate.

The desktop used to print ``via`` verbatim ("cost_mode auto preset", "ladder_of: [weak, default,
top]"), in English and with internal names, on a Portuguese interface. A category is closed and
translatable; the sentence stays for the CLI and for a tooltip.
"""

from __future__ import annotations

import pytest

from chimera.config import Settings
from chimera.providers.routing import VIA_KINDS, via_kind_of


@pytest.fixture(autouse=True)
def _no_discovered_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.providers.discovery.generic_providers", lambda environ=None: [])


def _settings(**kwargs: str) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("via", "kind"),
    [
        ("cost_mode auto preset", "preset"),
        ("ladder_of: [weak, default, top]", "ladder"),
        ("the measured escalation model (an OpenRouter key reaches it)", "escalation_measured"),
        ("backend hosted_verbalized default", "backend_default"),
        ("tier.top via fusion_config", "fusion_ladder"),
        ("the tier ladder (fusion_config)", "fusion_ladder"),
        ("request override", "override"),
        ("fusion_panel", "setting"),
        ("CHIMERA_DEFAULT_MODEL", "setting"),
        ("the product's default", "product_default"),
        ("voice_model is empty", "empty"),
        ("not differentiated today", "follows"),
        ("unavailable: ValueError", "unavailable"),
        ("cost_mode auto preset; effort from CHIMERA_ROLE_EFFORTS", "preset"),
        ("a sentence nobody wrote a rule for", "other"),
    ],
)
def test_a_via_sentence_falls_in_its_category(via: str, kind: str) -> None:
    assert via_kind_of(via) == kind
    assert kind in VIA_KINDS


def test_the_public_rows_carry_the_kind_and_none_is_other_by_default_settings() -> None:
    rows = _settings().routing_table().describe()
    assert rows
    for row in rows:
        assert row["via_kind"] == via_kind_of(row["via"])
        assert row["via_kind"] in VIA_KINDS
        assert row["via_kind"] != "other", f"{row['role']}: {row['via']!r} has no category"


def test_a_typed_setting_and_a_role_effort_keep_their_category() -> None:
    rows = {
        r["role"]: r for r in _settings(
            CHIMERA_ROLE_EFFORTS="fusion.judge=high", CHIMERA_DEFAULT_MODEL="openrouter/x/y",
        ).routing_table().describe()
    }
    assert rows["default"]["via_kind"] == "setting"
    assert "effort from" in rows["fusion.judge"]["via"]
    assert rows["fusion.judge"]["via_kind"] != "other"
