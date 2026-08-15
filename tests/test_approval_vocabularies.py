"""One env var, two settings, and six documented values that were all broken.

``CHIMERA_APPROVAL`` was declared as the ``validation_alias`` of **two** fields. Pydantic populated
both, and the two vocabularies share no value, so every documented setting failed in one direction
or the other. Measured across all six before the split:

| value | field it was written for | what actually happened |
|---|---|---|
| ``ask`` / ``allow`` / ``deny`` | ``approval_mode`` | ``ValidationError`` out of `deployment_posture` — and ``ask`` is that field's own documented default, so it killed every coding turn |
| ``always`` / ``suspicious`` / ``never`` | ``approval`` | arrived at ``approval_mode`` unrecognised, fell through to the ``ask`` branch, and headless — which is what cron is — **refused everything** |

The second row is the worse one. An owner writing "never stop and ask" got the exact opposite, and
got it as a refusal string the agent reads past, so the job reports success having done nothing.

They are not the same axis and never were. ``approval`` answers *when should a run pause for me* —
posture, owned by the desktop Settings screen, which writes that variable. ``approval_mode`` answers
*what happens when the approver is consulted* — policy, read by `solve` and every unattended
surface. The second one moved, because nothing writes it and nothing documented it, while renaming
the first would silently break settings already saved by the app.

Every test here fails against the pre-split code.
"""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.api.posture import deployment_posture
from chimera.config import Settings
from chimera.governance.approval import ApprovalLedger, approver_for
from chimera.governance.ledger import Decision, SequenceAssessment


def _settings(tmp_path: Path, **env: str) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path), **env)  # type: ignore[arg-type]


def _assessment() -> SequenceAssessment:
    return SequenceAssessment(True, Decision.REVIEW, "writes after reading untrusted content")


# --- the two variables are now independent --------------------------------------------------------


def test_the_two_settings_no_longer_share_one_variable(tmp_path: Path) -> None:
    """The bug in one assertion: setting one must not move the other."""
    posture = _settings(tmp_path, CHIMERA_APPROVAL="always")
    policy = _settings(tmp_path, CHIMERA_APPROVAL_MODE="allow")

    assert (posture.approval, posture.approval_mode) == ("always", "ask")
    assert (policy.approval, policy.approval_mode) == ("", "allow")


@pytest.mark.parametrize("value", ["always", "suspicious", "never"])
def test_every_documented_posture_value_survives_to_the_posture(tmp_path: Path, value: str) -> None:
    """These used to reach `approval_mode`, land in the `ask` branch and deny everything headless."""
    settings = _settings(tmp_path, CHIMERA_APPROVAL=value)

    resolved = deployment_posture(settings)  # used to raise for the other three

    assert settings.approval == value
    assert resolved.pause_always is (value == "always")
    assert resolved.pause_on_taint is (value == "suspicious")


@pytest.mark.parametrize(("value", "approves"), [("allow", True), ("deny", False), ("ask", False)])
def test_every_documented_policy_value_reaches_the_approver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, approves: bool
) -> None:
    """`ask` is False here because there is no terminal, which is what cron has — the fail-closed
    end, and the reason `ask` degrades to deny rather than to allow."""
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # headless
    settings = _settings(tmp_path, CHIMERA_APPROVAL_MODE=value)
    book = ApprovalLedger()

    assert approver_for(settings.approval_mode, book)(_assessment()) is approves


def test_the_posture_no_longer_dies_on_the_other_vocabulary(tmp_path: Path) -> None:
    """`CHIMERA_APPROVAL=ask` used to raise `ValidationError: 1 validation error for Posture` on
    every coding turn — an error naming neither the setting nor the fix, at request time rather
    than at startup."""
    for value in ("ask", "allow", "deny"):
        deployment_posture(_settings(tmp_path, CHIMERA_APPROVAL=value))  # must not raise


# --- a value from the wrong side is named, not guessed at ------------------------------------------


def test_a_governance_word_in_the_posture_variable_says_where_it_belongs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Falling back to "" is the same shape `governed_profile` uses for an unknown mode: a typo must
    not silently enable or silently disable something stricter than intended, and "" states nothing.

    The message has to name the OTHER variable. "invalid value" sends someone to the wrong file.
    """
    with caplog.at_level(logging.WARNING, logger="chimera.config"):
        settings = _settings(tmp_path, CHIMERA_APPROVAL="deny")

    assert settings.approval == ""
    assert "CHIMERA_APPROVAL_MODE" in caplog.text


def test_a_posture_word_in_the_governance_variable_says_where_it_belongs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="chimera.config"):
        settings = _settings(tmp_path, CHIMERA_APPROVAL_MODE="always")

    assert settings.approval_mode == "ask", "fell back to something other than the closed end"
    assert "CHIMERA_APPROVAL" in caplog.text


@pytest.mark.parametrize("field", ["CHIMERA_APPROVAL", "CHIMERA_APPROVAL_MODE"])
def test_a_typo_falls_back_rather_than_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, field: str
) -> None:
    """A misspelled env var must not stop the product from starting — but it must be audible.
    Silence here is how a fence someone believes they configured turns out not to exist."""
    with caplog.at_level(logging.WARNING, logger="chimera.config"):
        settings = _settings(tmp_path, **{field: "susipcious"})

    assert (settings.approval, settings.approval_mode) == ("", "ask")
    assert "susipcious" in caplog.text


def test_the_fallback_for_the_policy_is_the_closed_end(tmp_path: Path, monkeypatch: Any) -> None:
    """Direction matters more than the fallback existing. Falling back to `allow` would make a typo
    the most permissive configuration in the product."""
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    settings = _settings(tmp_path, CHIMERA_APPROVAL_MODE="alow")  # a plausible typo for `allow`
    book = ApprovalLedger()

    assert approver_for(settings.approval_mode, book)(_assessment()) is False
    assert book.blocked


# --- defaults are unchanged ------------------------------------------------------------------------


def test_a_deployment_that_sets_neither_behaves_exactly_as_before(tmp_path: Path) -> None:
    """The split must be invisible to everyone who never set the variable — which, since it was
    undocumented, is nearly everyone."""
    settings = _settings(tmp_path)

    assert settings.approval == ""
    assert settings.approval_mode == "ask"
    resolved = deployment_posture(settings)
    assert (resolved.pause_always, resolved.pause_on_taint) == (False, False)
