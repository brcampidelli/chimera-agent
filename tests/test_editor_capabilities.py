"""What a freshly downloaded app can tell you about itself.

The app is shipped as an installer. That is the exact situation where "the dependency should be
there" stops being evidence: the machine belongs to someone else, the sidecar was frozen by CI, and
the person who has just double-clicked it has no reason to know that inline completion wants a base
model or that diagnostics want `ruff`. So each capability answers present/absent WITH the command
that fixes absent — the same contract the external-agent list already keeps.
"""

from __future__ import annotations

from pathlib import Path

from chimera.api.config_api import doctor, editor_capabilities, is_editable
from chimera.config import Settings


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path), **kwargs)  # type: ignore[arg-type]


def test_every_capability_carries_the_command_that_enables_it(tmp_path: Path) -> None:
    found = editor_capabilities(_settings(tmp_path))

    assert {row["key"] for row in found} == {"diagnostics", "completion"}
    for row in found:
        assert row["hint"], f"{row['key']} reports availability with no way to act on it"
        assert row["label"] and row["detail"]


def test_completion_is_unavailable_when_no_model_is_named(tmp_path: Path) -> None:
    """And the hint then names the SETTING, not a pull command for a model nobody chose.

    "ollama pull" with an empty model is the kind of instruction that looks like help and cannot be
    followed.
    """
    found = {
        row["key"]: row for row in editor_capabilities(_settings(tmp_path, CHIMERA_COMPLETE_MODEL=""))
    }

    assert found["completion"]["available"] is False
    assert "CHIMERA_COMPLETE_MODEL" in found["completion"]["hint"]


def test_a_configured_completion_hints_the_pull_for_that_exact_model(tmp_path: Path) -> None:
    found = {
        row["key"]: row
        for row in editor_capabilities(_settings(tmp_path, CHIMERA_COMPLETE_MODEL="qwen2.5-coder:1.5b-base"))
    }

    assert found["completion"]["hint"] == "ollama pull qwen2.5-coder:1.5b-base"


def test_configured_and_available_are_not_the_same_claim(tmp_path: Path) -> None:
    """`probed` is what keeps the CLI from printing "available" for a server nobody reached.

    Diagnostics resolve a program, so that answer is measured. The completion model may live on
    another machine that is merely asleep; calling it available would be a promise the editor then
    fails to keep, and the user would go looking for the fault in the wrong place.
    """
    found = {row["key"]: row for row in editor_capabilities(_settings(tmp_path))}

    assert found["diagnostics"]["probed"] is True
    assert found["completion"]["probed"] is False


def test_doctor_reports_the_editor_alongside_the_external_agents(tmp_path: Path) -> None:
    # One place to answer "what works on this machine", not two.
    report = doctor(_settings(tmp_path))

    assert "editor" in report
    assert {row["key"] for row in report["editor"]} == {"diagnostics", "completion"}


def test_the_completion_model_is_editable_from_the_app() -> None:
    """Otherwise the only way to set it is a file the user has to be told about — and the thing they
    need to know (it must be a base tag) lives on the screen that cannot save it."""
    assert is_editable("CHIMERA_COMPLETE_MODEL")


def test_doctor_says_whether_a_spend_cap_could_work_here(tmp_path: Path) -> None:
    """The footgun this answers: the shipped default model has no list price, so a dollar cap would
    stop on its first call. Learning that from `doctor` costs nothing; learning it when a 3 a.m.
    cron job halts costs the job."""
    from chimera.api.config_api import pricing_capability

    unpriced = pricing_capability(_settings(tmp_path, CHIMERA_DEFAULT_MODEL="brand-new-model"))
    assert unpriced["available"] is False
    assert "brand-new-model" in unpriced["hint"]

    priced = pricing_capability(
        _settings(tmp_path, CHIMERA_DEFAULT_MODEL="openrouter/deepseek/deepseek-chat")
    )
    assert priced["available"] is True
    assert priced["hint"] == ""


def test_a_local_model_counts_as_priceable(tmp_path: Path) -> None:
    # It costs no provider dollars, so a cap over it is trivially satisfiable rather than broken.
    from chimera.api.config_api import pricing_capability

    found = pricing_capability(_settings(tmp_path, CHIMERA_DEFAULT_MODEL="ollama/llama3"))

    assert found["available"] is True
