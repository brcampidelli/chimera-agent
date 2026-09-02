"""`CHIMERA_GUARD_CHAT=` in a `.env` and the app does not start.

Twenty-six boolean settings, and an empty value for any one of them raises out of `get_settings()`:

    pydantic_core.ValidationError: 1 validation error for Settings
    CHIMERA_GUARD_CHAT
      Input should be a valid boolean, unable to interpret input [input_value='']

Not a hypothetical shape. Writing `VAR=` is how people turn a line off without deleting it, it is
what `export VAR=` leaves behind, and this repository's own `.env` ships with `OPENROUTER_API_KEY=`
empty. The failure is total — no CLI, no API, no desktop sidecar — and the message is a pydantic
traceback naming a type, not a sentence saying which line of which file to fix.

An empty variable is the absence of a value, which is what a default is for. It now reads as unset.

**Only for booleans.** For a string, `""` can be a real answer — an empty allowlist is not the same
as no allowlist — and sweeping every empty value into "unset" would change what those mean to buy a
fix for a type that has no empty case at all.

Found while building `bench/tool_defer`: arm A set the variable to `""` to mean off, and every one
of its five runs died in under a second with zero steps. The bench was measuring the harness.
"""

from __future__ import annotations

import pytest

from chimera.config import Settings


def test_an_empty_boolean_reads_as_unset() -> None:
    """The whole defect, on the field the bench tripped over."""
    assert Settings(CHIMERA_DEFER_TOOLS="").defer_tools is False


@pytest.mark.parametrize(
    "nome", ["CHIMERA_MCP_DEFER", "CHIMERA_GUARD_CHAT", "CHIMERA_COMPACT_SCHEMAS"]
)
def test_it_was_never_about_one_field(nome: str) -> None:
    """Named individually, because the fix is generic and a generic fix is the kind that silently
    stops covering a field somebody adds later with a different shape."""
    Settings(**{nome: ""})


def test_every_boolean_setting_survives_an_empty_value() -> None:
    """All twenty-six, from the model itself rather than a list that would drift.

    A hand-written list would pass forever while a new boolean added next month kept the defect.
    """
    booleanos = [
        (nome, campo)
        for nome, campo in Settings.model_fields.items()
        if campo.annotation is bool
    ]
    assert len(booleanos) > 20, "expected the boolean settings to still be there"

    for nome, campo in booleanos:
        alias = campo.validation_alias or nome
        Settings(**{str(alias): ""})


def test_a_default_that_is_true_stays_true() -> None:
    """Reading empty as "unset" must mean the DEFAULT, not `False`.

    Collapsing to False would be the easy version of this fix and would silently switch off every
    setting that defaults on — a security posture among them — for anybody who left a blank line
    in their `.env`.
    """
    ligados = [
        (nome, campo)
        for nome, campo in Settings.model_fields.items()
        if campo.annotation is bool and campo.default is True
    ]
    assert ligados, "expected at least one boolean that defaults to on"

    for nome, campo in ligados:
        alias = str(campo.validation_alias or nome)
        assert getattr(Settings(**{alias: ""}), nome) is True, alias


def test_a_real_value_still_wins() -> None:
    """The fix must not swallow values that were actually set."""
    assert Settings(CHIMERA_DEFER_TOOLS="1").defer_tools is True
    assert Settings(CHIMERA_DEFER_TOOLS="true").defer_tools is True
    assert Settings(CHIMERA_DEFER_TOOLS="0").defer_tools is False


def test_an_empty_string_setting_is_left_alone() -> None:
    """`""` is a real answer for a string, and the fix is scoped to booleans for that reason.

    An empty allowlist is not the same as no allowlist, and this asserts the fix did not quietly
    turn one into the other.
    """
    assert Settings(OPENROUTER_API_KEY="").openrouter_api_key == ""


def test_it_works_through_the_environment_and_not_only_through_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case that actually broke, which every test above misses.

    `Settings(CHIMERA_GUARD_CHAT="")` and `CHIMERA_GUARD_CHAT= chimera solve` reach pydantic by
    different routes, and only the second one ever happened to anybody. A fix verified through
    kwargs alone would be a fix for a call nobody makes — the same class of mistake as a test that
    exercises a mock instead of the path.
    """
    monkeypatch.setenv("CHIMERA_GUARD_CHAT", "")
    monkeypatch.setenv("CHIMERA_DEFER_TOOLS", "")

    settings = Settings()

    assert settings.defer_tools is False
    assert isinstance(settings.guard_chat, bool)


def test_the_environment_still_carries_a_real_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control for the one above: if empty reads as unset, a set value must still arrive."""
    monkeypatch.setenv("CHIMERA_DEFER_TOOLS", "1")

    assert Settings().defer_tools is True
