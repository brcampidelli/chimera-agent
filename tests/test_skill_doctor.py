"""``/skill-doctor``: what the skills a run is told about cost, and which are unused.

Covers the arithmetic (each skill's share is measured on the text the prompt carries), the order of
the report, what it says when nothing counted a skill (no record is not zero), the credit a Code turn
leaves on the cards it was told about, the route's auth and folder check, the reserved command name,
and the CLI. Nothing here calls a model: the same folder must always give the same report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.core.context_budget import estimate_tokens
from chimera.evolution import SkillStore
from chimera.evolution.card_retrieval import CardRetriever, cards_context_block
from chimera.evolution.code_turn_learning import CodeTurnRecall
from chimera.evolution.learned_skill import LearnedSkill
from chimera.skills import bundles
from chimera.skills.doctor import (
    KIND_BUNDLE,
    KIND_CARD,
    ORIGIN_PROJECT,
    ORIGIN_USER,
    SUGGEST_DISABLE,
    SUGGEST_KEEP,
    SUGGEST_NO_RECORD,
    SUGGEST_PROBATION,
    SUGGEST_READING_OFF,
    SUGGEST_SINCE_PROMOTION,
    build_report,
    report_for,
)


def _bundle(home: Path, name: str, description: str, **extra: Any) -> None:
    """An installed, switched-on bundle, written as `install` and `set_status` leave one."""
    root = bundles.bundles_root(home) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\nDo the thing.\n", encoding="utf-8")
    record: dict[str, Any] = {
        "name": name,
        "description": description,
        "status": "active",
        "origin": extra.pop("origin", "catalog"),
        "switched_on_at": "2026-11-01T00:00:00+00:00",
        **extra,
    }
    (root / "bundle.json").write_text(json.dumps(record), encoding="utf-8")


def _tokens(text: str) -> int:
    return estimate_tokens([{"role": "user", "content": text}])


def _card(name: str, description: str, *, status: str = "active") -> LearnedSkill:
    return LearnedSkill(name=name, description=description, do="x", check="y", status=status)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    _bundle(h, "short-skill", "Short.")
    _bundle(h, "long-skill", "A much longer description " * 20, origin="plugin", source="plugin:demo")
    return h


# --- arithmetic --------------------------------------------------------------------------------


def test_each_bundle_costs_its_prompt_line_under_the_apps_estimator(home: Path) -> None:
    by_name = {s.name: s for s in build_report(home).skills}
    line = bundles._context_line(next(b for b in bundles.active(home) if b.name == "long-skill"))
    assert by_name["long-skill"].tokens == _tokens(line)
    assert by_name["long-skill"].kind == KIND_BUNDLE
    assert by_name["long-skill"].every_turn is True


def test_the_per_turn_total_is_the_measured_prompt_block(home: Path) -> None:
    report = build_report(home)
    assert report.tokens_per_turn == _tokens(bundles.prompt_block(home))
    assert report.tokens_per_turn > sum(s.tokens for s in report.skills)


def test_a_card_costs_its_body_and_the_heading_and_instruction_are_paid_once_per_task(home: Path) -> None:
    card = _card("writes-tests", "Writes focused tests before code. " * 10)
    report = build_report(home, cards=[card], cards_read=True, cards_k=1, max_lines=2)
    row = next(s for s in report.skills if s.kind == KIND_CARD)
    assert row.tokens == _tokens(f"[writes-tests]\n{card.card_text(max_lines=2)}")
    # The task total is the block the prompt carries: heading and instruction included, once.
    assert report.cards_tokens_per_task_max == _tokens(cards_context_block([card], max_lines_per_card=2))
    assert report.cards_tokens_per_task_max > row.tokens


def test_a_cards_cost_is_outside_the_per_turn_total(home: Path) -> None:
    card = _card("writes-tests", "Writes focused tests before code.")
    report = build_report(home, cards=[card], cards_read=True, cards_k=3)
    assert report.tokens_per_turn == _tokens(bundles.prompt_block(home))


# --- ordering ----------------------------------------------------------------------------------


def test_report_is_largest_first_with_ties_broken_by_name(home: Path) -> None:
    _bundle(home, "twin-b", "Same words here.")
    _bundle(home, "twin-a", "Same words here.")
    report = build_report(home)
    tokens = [s.tokens for s in report.skills]
    assert tokens == sorted(tokens, reverse=True)
    assert [s.name for s in report.skills if s.name.startswith("twin-")] == ["twin-a", "twin-b"]


def test_no_skills_at_all_is_an_empty_report_not_an_error(tmp_path: Path) -> None:
    report = build_report(tmp_path)
    assert report.skills == ()
    assert report.tokens_per_turn == 0
    assert report.to_dict()["usage_days"] is None


# --- no usage data: absent is not zero ---------------------------------------------------------


def test_a_bundle_is_never_called_idle_because_nothing_counted_it(home: Path) -> None:
    for skill in build_report(home).skills:
        assert skill.injected_runs is None
        assert skill.suggestion == SUGGEST_NO_RECORD


def test_a_card_with_no_counter_is_no_record_not_idle_even_with_reading_on() -> None:
    report = build_report(
        Path("/nonexistent"), cards=[_card("new-card", "Plans work.")], runs={}, cards_read=True, cards_k=2
    )
    (row,) = [s for s in report.skills if s.kind == KIND_CARD]
    assert row.injected_runs is None
    assert row.suggestion == SUGGEST_NO_RECORD


def test_a_card_credited_zero_runs_is_suggested_for_switching_off() -> None:
    report = build_report(
        Path("/nonexistent"),
        cards=[_card("idle-card", "Reviews diffs."), _card("used-card", "Writes tests.")],
        runs={"idle-card": 0, "used-card": 7},
        cards_read=True,
        cards_k=3,
    )
    by_name = {s.name: s for s in report.skills}
    assert by_name["idle-card"].suggestion == SUGGEST_DISABLE
    assert by_name["used-card"].suggestion == SUGGEST_KEEP
    assert by_name["used-card"].injected_runs == 7


def test_a_card_on_probation_is_never_suggested_for_switching_off() -> None:
    report = build_report(
        Path("/nonexistent"),
        cards=[_card("young-card", "Plans work.", status="provisional")],
        runs={"young-card": 0},
        cards_read=True,
        cards_k=1,
    )
    (row,) = [s for s in report.skills if s.kind == KIND_CARD]
    assert row.suggestion == SUGGEST_PROBATION


def test_a_card_promoted_and_not_credited_since_is_no_record_since_promotion_not_idle(
    tmp_path: Path,
) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_card("promoted-card", "Writes tests.", status="provisional"))
    store.add(_card("plain-idle", "Reviews diffs."))
    store.record_use("plain-idle", success=False)  # credited once, then never again: a real record
    assert store.promote("promoted-card")
    report = build_report(
        tmp_path,
        cards=store.retrievable(),
        runs=store.credited_runs(),
        promoted=store.counted_since_promotion(),
        cards_read=True,
        cards_k=2,
    )
    by_name = {s.name: s for s in report.skills}
    assert by_name["promoted-card"].suggestion == SUGGEST_SINCE_PROMOTION
    assert by_name["promoted-card"].injected_runs == 0
    assert by_name["plain-idle"].suggestion == SUGGEST_KEEP


def test_a_promotion_marker_survives_a_reload_of_the_store(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_card("promoted-card", "Writes tests.", status="provisional"))
    store.promote("promoted-card")
    assert SkillStore(tmp_path / "skills.json").counted_since_promotion() == {"promoted-card"}


def test_with_card_reading_off_no_card_is_suggested_at_all() -> None:
    report = build_report(
        Path("/nonexistent"), cards=[_card("idle-card", "Reviews diffs.")], runs={"idle-card": 0}
    )
    (row,) = [s for s in report.skills if s.kind == KIND_CARD]
    assert row.suggestion == SUGGEST_READING_OFF
    assert report.cards_tokens_per_task_max == 0


# --- scope and origin --------------------------------------------------------------------------


def test_a_pack_narrows_the_bundles_it_does_not_name(home: Path) -> None:
    report = build_report(home, only={"short-skill"})
    assert [s.name for s in report.skills] == ["short-skill"]
    assert report.tokens_per_turn == _tokens(bundles.prompt_block(home, only={"short-skill"}))


def test_a_plugin_outside_the_plugin_scope_is_neither_listed_nor_paid_for(home: Path) -> None:
    report = build_report(home, plugin_scope={"another-plugin"})
    assert [s.name for s in report.skills] == ["short-skill"]
    assert report.tokens_per_turn == _tokens(
        bundles.prompt_block(home, plugin_scope={"another-plugin"})
    )


def test_a_discovered_skill_is_the_projects_only_under_its_folder_not_a_string_prefix(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "app"
    workspace.mkdir()
    _bundle(home, "in-project", "In.", origin="discovered", source=str(workspace / ".claude" / "skills" / "a"))
    _bundle(home, "sibling", "Sibling.", origin="discovered", source=str(tmp_path / "app2" / ".claude" / "skills" / "b"))
    by_name = {s.name: s for s in build_report(home, workspace=workspace).skills}
    assert by_name["in-project"].origin == ORIGIN_PROJECT
    assert by_name["sibling"].origin == ORIGIN_USER


# --- the credit a Code turn leaves -------------------------------------------------------------


def test_a_code_turn_credits_the_cards_it_was_told_about(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_card("reread-premises", "reread trick premises"))
    store.add(_card("unrelated", "bake sourdough bread"))
    retriever = CardRetriever(store, k=3, min_overlap=0)
    recall = CodeTurnRecall(retriever, tmp_path)
    recall.card_context("reread the trick premises")

    recall.record_turn_outcome(verdict="passed", steps=2)
    runs = store.credited_runs()
    assert runs == {"reread-premises": 1}
    assert "unrelated" not in runs


def test_a_turn_a_gate_stopped_credits_nothing(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_card("reread-premises", "reread trick premises"))
    recall = CodeTurnRecall(CardRetriever(store, k=3, min_overlap=0), tmp_path)
    recall.card_context("reread the trick premises")
    recall.record_turn_outcome(verdict="passed", steps=0)
    assert store.credited_runs() == {}


def test_a_failed_or_stopped_turn_that_ran_its_loop_still_credits_its_cards(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_card("reread-premises", "reread trick premises"))
    recall = CodeTurnRecall(CardRetriever(store, k=3, min_overlap=0), tmp_path)
    recall.card_context("reread the trick premises")
    recall.record_turn_outcome(verdict="failed", steps=3)
    assert store.credited_runs() == {"reread-premises": 1}


def test_a_passed_check_is_a_success_and_an_unverified_run_is_a_use_only(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_card("reread-premises", "reread trick premises"))
    recall = CodeTurnRecall(CardRetriever(store, k=3, min_overlap=0), tmp_path)
    recall.card_context("reread the trick premises")
    recall.record_turn_outcome(verdict="unverified", steps=1)
    recall.card_context("reread the trick premises")
    recall.record_turn_outcome(verdict="passed", steps=1)
    (row,) = store.stats_overview()
    assert row["uses"] == 2
    assert row["successes"] == 1


# --- the report read from the app's settings ---------------------------------------------------


def test_report_for_reads_the_apps_settings_and_is_repeatable(home: Path) -> None:
    class _Settings:
        def __init__(self, root: Path) -> None:
            self.home = root
            self.skill_cards = False
            self.skill_cards_k = 0

    first = report_for(_Settings(home), None).to_dict()
    assert first == report_for(_Settings(home), None).to_dict()
    assert [s["name"] for s in first["skills"]] == ["long-skill", "short-skill"]


# --- the route: auth and the folder check ------------------------------------------------------


def _client(monkeypatch: pytest.MonkeyPatch, home: Path, token: str | None = None) -> Any:
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    if token is None:
        monkeypatch.delenv("CHIMERA_SERVER_TOKEN", raising=False)
    else:
        monkeypatch.setenv("CHIMERA_SERVER_TOKEN", token)
    get_settings.cache_clear()
    return TestClient(build_api_app(lambda: ChatSession(object()), settings=Settings()))


def test_the_route_refuses_a_caller_without_the_token_when_one_is_set(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(monkeypatch, home, token="s3cret")
    try:
        assert client.get("/api/skills/doctor").status_code == 401
        ok = client.get("/api/skills/doctor", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200, ok.text
        assert [s["name"] for s in ok.json()["skills"]] == ["long-skill", "short-skill"]
    finally:
        from chimera.config import get_settings

        get_settings.cache_clear()


def test_the_route_answers_for_a_folder_that_exists_and_refuses_one_that_does_not(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(monkeypatch, home)
    try:
        here = client.get("/api/skills/doctor", params={"project": str(tmp_path)})
        assert here.status_code == 200, here.text
        gone = client.get("/api/skills/doctor", params={"project": str(tmp_path / "missing")})
        assert gone.status_code == 400
        assert gone.json()["detail"] == "workspace not found"
    finally:
        from chimera.config import get_settings

        get_settings.cache_clear()


# --- the reserved name -------------------------------------------------------------------------


def test_skill_doctor_is_a_reserved_name_so_a_project_file_cannot_shadow_it() -> None:
    from chimera.api.commands_api import reserved_command_names

    assert "skill-doctor" in reserved_command_names()
    assert "code-review" in reserved_command_names()


# --- the CLI -----------------------------------------------------------------------------------


def test_cli_prints_the_table_and_names_the_no_record_state(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["skills", "doctor"])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert "long-skill" in result.output and "short-skill" in result.output
    assert "no record" in result.output
    assert "Suggestions only" in result.output


def test_cli_json_is_the_same_report_the_route_returns(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.cli.main import app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["skills", "doctor", "--json"])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tokens_per_turn"] == _tokens(bundles.prompt_block(home))
    assert [s["name"] for s in payload["skills"]] == ["long-skill", "short-skill"]


def test_cli_refuses_a_project_folder_that_does_not_exist(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["skills", "doctor", "--project", str(tmp_path / "missing")])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "workspace not found" in result.output
