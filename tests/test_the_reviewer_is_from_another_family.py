"""The reviewer comes from a different model family than the author, unless someone chose otherwise.

`chimera.api.roles.review_model_for` stops the reviewer being the same model as the editor. A
sibling from the same vendor shares the author's training and taste, so the family is the unit here
(study 25 §2.9). A slug is a route, so the family is read from the vendor segment when there is one.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from chimera.cli import review_cmd
from chimera.config import get_settings
from chimera.review import CautiousVerifier, choose_reviewer, collect, model_family, review
from tests.review_fakes import FakeBackend, finder_json, finding, repo_with_change

DEEPSEEK = "openrouter/deepseek/deepseek-v4-flash-0731"
GLM = "openrouter/z-ai/glm-5.3"
MISTRAL = "openrouter/mistralai/mistral-small-3.2-24b-instruct"
OPUS = "openrouter/anthropic/claude-opus-5"


@pytest.mark.parametrize(("slug", "family"), [
    (DEEPSEEK, "deepseek"),
    ("deepseek/deepseek-chat", "deepseek"),
    (GLM, "zhipu"),
    (OPUS, "anthropic"),
    ("anthropic/claude-opus-5", "anthropic"),
    ("gemini/gemini-3.6-flash", "google"),
    ("openai/gpt-5.6", "openai"),
    ("openrouter/openai/gpt-oss-20b", "openai"),
    (MISTRAL, "mistral"),
    ("openrouter/moonshotai/kimi-k2", "moonshot"),
    ("ollama_chat/qwen3:4b", "qwen"),
    ("ollama/llama3", "meta"),
])
def test_a_slug_is_read_to_its_family(slug: str, family: str) -> None:
    assert model_family(slug) == family


def test_the_default_is_the_strongest_rung_of_another_family() -> None:
    choice = choose_reviewer(DEEPSEEK, ladder=[GLM, DEEPSEEK, MISTRAL])

    assert (choice.model, choice.source, choice.same_family) == (GLM, "tier ladder", False)


def test_a_ladder_of_one_family_falls_through_to_the_panel() -> None:
    choice = choose_reviewer(DEEPSEEK, ladder=[DEEPSEEK, DEEPSEEK], panel=[DEEPSEEK, OPUS])

    assert (choice.model, choice.family) == (OPUS, "anthropic")


def test_a_model_no_key_can_call_is_skipped() -> None:
    choice = choose_reviewer(DEEPSEEK, ladder=[GLM, MISTRAL], reachable={MISTRAL})

    assert choice.model == MISTRAL


def test_with_nothing_else_reachable_the_author_reviews_and_it_is_said(tmp_path: Path) -> None:
    choice = choose_reviewer(DEEPSEEK, ladder=[GLM], reachable=set())
    report = review(collect(repo_with_change(tmp_path)), FakeBackend(finder_json([])), choice,
                    CautiousVerifier(FakeBackend(""), choice.model))

    assert (choice.model, choice.source, choice.same_family) == (DEEPSEEK, "fallback", True)
    assert report.reviewer.same_family is True
    assert any("shares its blind spots" in note for note in report.notes)


def test_a_named_reviewer_is_honoured_even_from_the_authors_family() -> None:
    choice = choose_reviewer(DEEPSEEK, explicit="deepseek/deepseek-chat", ladder=[GLM])

    assert (choice.model, choice.source) == ("deepseek/deepseek-chat", "flag")
    assert choice.same_family is True


def test_both_stages_run_on_the_reviewer_not_the_author(tmp_path: Path) -> None:
    choice = choose_reviewer(DEEPSEEK, ladder=[GLM])
    backend = FakeBackend(finder_json([finding("calc.py", 11)]))

    verifier = CautiousVerifier(backend, choice.model)
    review(collect(repo_with_change(tmp_path)), backend, choice, verifier)

    assert {model for _, _, model in backend.calls} == {GLM}


@pytest.fixture
def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_REVIEW_MODEL", "CHIMERA_DEFAULT_MODEL", "CHIMERA_WEAK_MODEL",
                "CHIMERA_MID_MODEL", "CHIMERA_ORCHESTRATOR_MODEL", "CHIMERA_FUSION_PANEL"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.mark.parametrize("mode", ["auto", "balanced", "cheap", "premium"])
def test_the_command_picks_another_family_under_every_cost_mode(
    _settings: pytest.MonkeyPatch, mode: str
) -> None:
    _settings.setenv("CHIMERA_COST_MODE", mode)
    get_settings.cache_clear()
    author = get_settings().default_model

    choice = review_cmd._reviewer(author, "")

    assert model_family(author) == "deepseek"
    assert choice.family != "deepseek", choice


def test_the_setting_names_the_reviewer(_settings: pytest.MonkeyPatch) -> None:
    _settings.setenv("CHIMERA_REVIEW_MODEL", OPUS)
    get_settings.cache_clear()

    choice = review_cmd._reviewer(DEEPSEEK, "")

    assert (choice.model, choice.source) == (OPUS, "setting")
