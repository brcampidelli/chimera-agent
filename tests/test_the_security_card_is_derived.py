"""The security card reads what the deciding functions return, never a second copy of the rules.

For four configurations, each field of `safety_overview` is compared with the function production
decides with: `bypass.floor_categories` (the same checks `bypass_safe` runs), `get_sandbox` (so
`auto` on Windows is `local`), `profile.surface_governance` (whose `resolve_mode` is what
`govern_step` uses) and the live switches. Audit 2026-11, a07-14.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.posture import Posture, describe, safety_overview
from chimera.config import Settings
from chimera.core.bypass_switches import BypassSwitches
from chimera.governance.bypass import floor_categories
from chimera.governance.hooks import _sandbox_name
from chimera.governance.profile import CARD_SURFACES, resolve_mode, surface_governance
from chimera.sandbox import get_sandbox
from chimera.sandbox.confirm import sandbox_is_isolated

CASES = [
    pytest.param("off", "auto", False, id="off-auto-no-bypass"),
    pytest.param("observe", "auto", True, id="observe-auto-bypass"),
    pytest.param("enforce", "local", True, id="enforce-local-bypass"),
    pytest.param("enforce", "auto", False, id="enforce-auto-no-bypass"),
]


@pytest.mark.parametrize("governance, sandbox, bypass_on", CASES)
def test_every_field_is_what_the_code_resolves(
    governance: str, sandbox: str, bypass_on: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Windows has no OS sandbox: `auto` must resolve, and be shown, as the host.
    monkeypatch.setattr("chimera.sandbox.os_sandbox.os_sandbox_available", lambda: False)
    settings = Settings(
        CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_GOVERNANCE=governance, CHIMERA_SANDBOX=sandbox,
    )
    clock_now = [100.0]
    switches = BypassSwitches(tmp_path / "home" / "code_bypass_switches.json", clock=lambda: clock_now[0])
    if bypass_on:
        switches.set_project("/repo/shop", True, ttl_seconds=60)

    card = safety_overview(settings, switches)

    assert card.floor == floor_categories()
    assert "outside_reach" in card.floor and card.floor_lexical == ["outside_reach"]
    real = get_sandbox(settings)
    assert card.sandbox == _sandbox_name(real) == "local"
    assert card.sandbox_isolated is bool(sandbox_is_isolated(real)) is False
    assert card.sandbox_configured == sandbox
    assert [g.surface for g in card.governance] == list(CARD_SURFACES)
    for row in card.governance:
        assert (row.kernel, row.taint) == surface_governance(settings, row.surface)
    code = next(g for g in card.governance if g.surface == "code")
    assert code.kernel == resolve_mode(settings) == governance
    assert [(b.scope, b.id, b.expires_at) for b in card.bypass] == [
        (row.scope, row.key, row.expires_at) for row in switches.live_entries()
    ]
    assert bool(card.bypass) is bypass_on
    if bypass_on:
        assert card.bypass[0].expires_at == pytest.approx(160.0)
        clock_now[0] = 161.0
        assert safety_overview(settings, switches).bypass == [], "an expired switch leaves the card"


def test_the_posture_answer_carries_the_card(tmp_path: Path) -> None:
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    facts = describe(Posture(), tmp_path, settings)
    assert facts.safety is not None
    assert facts.safety.floor == floor_categories()


def test_the_route_serves_the_card(tmp_path: Path) -> None:
    from typing import Any

    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.interface.session import ChatSession

    class _Mute:
        def run(self, task: str, **_kw: Any) -> Any:  # pragma: no cover - no turn runs here
            raise AssertionError("no turn should run")

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(build_api_app(lambda: ChatSession(_Mute()), workspace=tmp_path, settings=settings))
    body = client.post("/api/security/overview").json()
    assert body["floor"] == floor_categories()
    assert [g["surface"] for g in body["governance"]] == list(CARD_SURFACES)
