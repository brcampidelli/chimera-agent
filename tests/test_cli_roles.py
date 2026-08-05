"""`chimera solve --profile / --role-models` — the flags the role bench needs to exist at all.

Role routing shipped in the API first, and `bench/role_routing` drives the CLI. Without these flags
the pre-registered arms could not be expressed, so the bench would have had to grow its own copy of
the routing — and a benchmark that exercises a second implementation is measuring something the
product does not ship. Hence the test that both surfaces resolve through the same function.
"""

from __future__ import annotations

from typing import Any

import pytest
import typer

from chimera.cli.main import _resolve_cli_roles
from chimera.config import Settings


def _settings() -> Settings:
    return Settings(CHIMERA_HOME="/tmp/chimera-cli-roles")


def test_no_flags_means_no_routing() -> None:
    """Every existing `chimera solve` invocation keeps running on one model."""
    plan = _resolve_cli_roles(None, None, _settings())
    assert plan.profile is None
    assert plan.models.edit is None and plan.models.plan is None


def test_a_profile_fills_every_role_from_the_tier_ladder() -> None:
    models = _resolve_cli_roles("balanced", None, _settings()).models
    assert models.explore and models.plan and models.edit and models.review


def test_overrides_parse_and_merge_over_the_profile() -> None:
    plan = _resolve_cli_roles("balanced", "edit=vendor/x,review=vendor/y", _settings())
    assert plan.models.edit == "vendor/x" and plan.models.review == "vendor/y"
    assert plan.models.explore  # untouched roles keep the profile's choice


def test_blank_entries_and_stray_commas_are_ignored() -> None:
    plan = _resolve_cli_roles(None, "edit=vendor/x, ,", _settings())
    assert plan.models.edit == "vendor/x"


def test_a_misspelled_role_is_refused_rather_than_ignored() -> None:
    """The failure this prevents is the quiet one: a typo'd role produces a run that looks routed,
    reports itself as routed, and is not — which would silently corrupt a bench arm."""
    with pytest.raises(typer.BadParameter, match="unknown role"):
        _resolve_cli_roles(None, "edt=vendor/x", _settings())


def test_verify_is_refused_as_a_role_with_the_reason() -> None:
    with pytest.raises(typer.BadParameter, match="no model to choose"):
        _resolve_cli_roles(None, "verify=vendor/x", _settings())


def test_a_malformed_pair_is_refused() -> None:
    with pytest.raises(typer.BadParameter, match="role=model"):
        _resolve_cli_roles(None, "edit", _settings())
    with pytest.raises(typer.BadParameter, match="role=model"):
        _resolve_cli_roles(None, "edit=", _settings())


def test_an_unknown_profile_is_refused() -> None:
    with pytest.raises(typer.BadParameter, match="unknown profile"):
        _resolve_cli_roles("cheapest", None, _settings())


def test_the_cli_and_the_desktop_resolve_through_the_same_function() -> None:
    """One resolver, two surfaces. A bench that drove a second implementation of the routing would
    measure something nobody uses, which is the quiet way a benchmark stops being about the product.
    """
    from chimera.api.roles import RoleModels
    from chimera.api.roles import resolve as api_resolve

    cli = _resolve_cli_roles("max", "edit=vendor/x", _settings())
    api = api_resolve("max", _settings(), RoleModels(edit="vendor/x"))
    assert cli.models == api.models and cli.profile == api.profile


def test_the_help_advertises_both_flags() -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    out = CliRunner().invoke(app, ["solve", "--help"]).output
    assert "--profile" in out and "--role-models" in out


def test_fused_if_only_wraps_when_asked(monkeypatch: Any) -> None:
    """`_fused_if` is only ever reached for the two TOOL-FREE roles. Asserted because the failure it
    prevents — fusing a turn that carries tools — is silent: the router would send it to a single
    model anyway, and the run would report a panel that never convened."""
    from chimera.cli.main import _fused_if
    from chimera.fusion import FusionEngine
    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    assert _fused_if(gateway, False, gateway) is gateway
    assert isinstance(_fused_if(gateway, True, gateway), FusionEngine)
