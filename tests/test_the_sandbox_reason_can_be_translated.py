"""The Security screen must be able to say WHY in its reader's language.

The endpoint returned one English sentence and the panel printed it. Measured in the shipped app:
*"Windows has no OS sandbox in Chimera…"* rendered on a Portuguese UI, one line below the panel's
own translated prose, on the single screen a person opens to learn what protects them.

The fix is the shape this repo already uses for ``PostureFacts.fell_back_reason``: a machine-readable
code the client translates, with the English sentence riding along as the fallback so a cause the
client has never heard of degrades to English rather than to a blank line.

Free: no model call, no network, no sandbox actually built.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

import pytest

from chimera.api.schemas import SandboxStateOut
from chimera.sandbox.os_sandbox import unavailable_cause

CAUSES = ("", "windows", "bwrap_missing", "userns_refused", "seatbelt_missing", "unsupported_os")


def _sandbox(tmp_path: Path) -> dict[str, Any]:
    from tests.test_api import _client  # noqa: PLC0415

    return dict(_client(tmp_path).get("/api/governance/sandbox").json())


# --- the endpoint ------------------------------------------------------------------------------


def test_the_endpoint_reports_a_code_beside_the_sentence(tmp_path: Path) -> None:
    body = _sandbox(tmp_path)

    assert "reason_code" in body, "the screen has nothing to translate"
    assert body["reason_code"] in (*CAUSES, "no_container")


def test_a_machine_with_no_boundary_says_which_cause(tmp_path: Path) -> None:
    """A code is only useful when it is populated exactly where the sentence was."""
    body = _sandbox(tmp_path)

    if body["isolated"]:
        pytest.skip("this machine has a real sandbox, so there is no cause to report")
    assert body["reason_code"], "no boundary applies and the panel is told nothing translatable"
    assert body["reason"], "the English fallback went missing, so an unknown code renders blank"


def test_the_code_agrees_with_the_sentence(tmp_path: Path) -> None:
    """Two fields, one fact. They are produced together for this reason — maintained apart they
    drift, and the drift surfaces as a screen confidently explaining the wrong cause."""
    body = _sandbox(tmp_path)

    if body["isolated"] or body["reason_code"] == "no_container":
        pytest.skip("not an OS-sandbox cause")
    code, sentence = unavailable_cause()
    assert body["reason_code"] == code
    assert body["reason"] == sentence


# --- the pair cannot drift, on every platform and not just this one -----------------------------
#
# Forced, because the machine running the suite has ONE answer: in WSL bubblewrap works, so
# `unavailable_cause()` returns ("", "") and every assertion about a cause passes without ever
# reaching one. A first version of these tests did exactly that — two sabotages walked straight
# through them — which is the same lesson as always: an instrument that cannot exhibit the effect
# produces no evidence about it.

PLATAFORMAS = [
    ("Windows", None, "windows"),
    ("Linux", None, "bwrap_missing"),
    ("Linux", "/usr/bin/bwrap", "userns_refused"),
    ("Darwin", None, "seatbelt_missing"),
    ("Haiku", None, "unsupported_os"),
]


@pytest.fixture
def sem_sandbox(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Force "no OS sandbox here", then let each case say which platform it is."""
    from chimera.sandbox import os_sandbox as mod

    monkeypatch.setattr(mod, "os_sandbox_available", lambda: False)

    def _forcar(system: str, bwrap: str | None) -> None:
        monkeypatch.setattr(mod.platform, "system", lambda: system)
        monkeypatch.setattr(mod.shutil, "which", lambda _name: bwrap)

    return _forcar


@pytest.mark.parametrize(("system", "bwrap", "esperado"), PLATAFORMAS)
def test_every_platform_reports_its_own_cause(
    sem_sandbox: Any, system: str, bwrap: str | None, esperado: str
) -> None:
    sem_sandbox(system, bwrap)

    code, sentence = unavailable_cause()

    assert code == esperado
    assert sentence, "a cause with no sentence renders as an empty line for an unknown code"


@pytest.mark.parametrize(("system", "bwrap", "esperado"), PLATAFORMAS)
def test_the_code_is_one_the_schema_admits(
    sem_sandbox: Any, system: str, bwrap: str | None, esperado: str
) -> None:
    """The ``Literal`` is what reaches TypeScript. A cause the schema does not list arrives at the
    client as a code it can neither translate nor type."""
    sem_sandbox(system, bwrap)
    code, _ = unavailable_cause()
    admitted = SandboxStateOut.model_fields["reason_code"].annotation

    assert code in admitted.__args__  # type: ignore[union-attr]


@pytest.mark.parametrize(("system", "bwrap", "esperado"), PLATAFORMAS)
def test_the_sentence_matches_the_cause_it_names(
    sem_sandbox: Any, system: str, bwrap: str | None, esperado: str
) -> None:
    """Code and sentence are produced together so they cannot drift — but "cannot" is a claim, and
    this is what checks it: each sentence has to mention the thing its code is about."""
    sem_sandbox(system, bwrap)
    marcas = {
        "windows": "windows",
        "bwrap_missing": "not installed",
        "userns_refused": "namespace",
        "seatbelt_missing": "sandbox-exec",
        "unsupported_os": "no os sandbox",
    }

    _, sentence = unavailable_cause()

    assert marcas[esperado] in sentence.lower()


def test_an_available_sandbox_reports_no_cause_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Either both fields or neither. One alone is what renders an empty line where an explanation
    should be."""
    from chimera.sandbox import os_sandbox as mod

    monkeypatch.setattr(mod, "os_sandbox_available", lambda: True)

    assert unavailable_cause() == ("", "")


@pytest.mark.skipif(platform.system() != "Windows", reason="the cause is per platform")
def test_windows_reports_the_windows_cause() -> None:
    """The measured case, pinned: this is the machine the defect was found on."""
    code, sentence = unavailable_cause()

    assert code == "windows"
    assert "docker" in sentence.lower(), "the one actionable instruction left the sentence"


# --- every cause has somewhere to be translated -------------------------------------------------


def test_every_cause_has_a_translation_key_in_every_language() -> None:
    """The gap this fix exists to close, checked for all ten languages rather than for English.

    A code with no entry falls back to English by design — correct as a safety net and wrong as a
    steady state, and the difference is invisible unless something counts.
    """
    i18n = Path("apps/desktop/src/lib/i18n.tsx").read_text(encoding="utf-8")
    idiomas = i18n.count('"governance.sandbox.stillApplies"')
    assert idiomas == 10, f"expected ten language blocks, found {idiomas}"

    for code in (*CAUSES, "no_container"):
        if not code:
            continue
        chave = f'"governance.sandbox.why.{code}"'
        assert i18n.count(chave) == idiomas, (
            f"{code} is translated in {i18n.count(chave)} of {idiomas} languages"
        )
