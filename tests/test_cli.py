"""Tests for the CLI surface (no network calls)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chimera import __version__
from chimera.cli.main import app
from chimera.config import get_settings

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_models_command_lists_panel() -> None:
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    assert "fusion panel" in result.stdout


def test_doctor_ready_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Ready" in result.stdout
    finally:
        get_settings.cache_clear()


def test_tools_command_lists_native_tools() -> None:
    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0
    assert "read_file" in result.stdout
    assert "run_shell" in result.stdout


def test_skills_command_lists_builtin_skills() -> None:
    result = runner.invoke(app, ["skills"])
    assert result.exit_code == 0
    assert "complete_code" in result.stdout


def test_migrate_dry_run(tmp_path: Path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  default: m/x\n", encoding="utf-8")
    result = runner.invoke(app, ["migrate", "hermes", str(home)])
    assert result.exit_code == 0
    assert "dry-run" in result.stdout


def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "chimera_home"))
    get_settings.cache_clear()


def test_cron_add_and_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolated_home(monkeypatch, tmp_path)
    try:
        added = runner.invoke(app, ["cron", "add", "daily", "0 9 * * *", "run report"])
        assert added.exit_code == 0
        assert "added" in added.stdout

        listed = runner.invoke(app, ["cron", "list"])
        assert listed.exit_code == 0
        assert "daily" in listed.stdout

        jobs_file = tmp_path / "chimera_home" / "scheduler" / "jobs.json"
        assert jobs_file.exists()
        jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
        assert jobs[0]["name"] == "daily"
    finally:
        get_settings.cache_clear()


def test_cron_add_invalid_expression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolated_home(monkeypatch, tmp_path)
    try:
        result = runner.invoke(app, ["cron", "add", "bad", "not a cron", "x"])
        assert result.exit_code == 1
    finally:
        get_settings.cache_clear()


def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()


def test_fuse_without_key_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    try:
        result = runner.invoke(app, ["fuse", "hello"])
        assert result.exit_code == 1
    finally:
        get_settings.cache_clear()


def test_solve_without_key_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    try:
        result = runner.invoke(app, ["solve", "do a thing"])
        assert result.exit_code == 1
    finally:
        get_settings.cache_clear()


def test_bench_without_key_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    try:
        result = runner.invoke(app, ["bench"])
        assert result.exit_code == 1
    finally:
        get_settings.cache_clear()


def test_crew_without_key_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    try:
        result = runner.invoke(app, ["crew", "design a thing"])
        assert result.exit_code == 1
    finally:
        get_settings.cache_clear()


def test_meta_without_key_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    try:
        result = runner.invoke(app, ["meta", "an agent for research"])
        assert result.exit_code == 1
    finally:
        get_settings.cache_clear()


def test_guard_command() -> None:
    blocked = runner.invoke(app, ["guard", "rm -rf /"])
    assert blocked.exit_code == 0
    assert "BLOCK" in blocked.stdout
    allowed = runner.invoke(app, ["guard", "ls -la"])
    assert "ALLOW" in allowed.stdout


def test_memory_add_list_search(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolated_home(monkeypatch, tmp_path)
    try:
        added = runner.invoke(app, ["memory", "add", "Alex prefers PT-BR"])
        assert added.exit_code == 0
        assert "ADD" in added.stdout

        listed = runner.invoke(app, ["memory", "list"])
        assert "PT-BR" in listed.stdout

        found = runner.invoke(app, ["memory", "search", "prefers"])
        assert "PT-BR" in found.stdout
    finally:
        get_settings.cache_clear()


def test_cron_learn_empty_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolated_home(monkeypatch, tmp_path)
    try:
        result = runner.invoke(app, ["cron", "learn"])
        assert result.exit_code == 0
        assert "no recurring" in result.stdout
    finally:
        get_settings.cache_clear()


def test_kanban_add_board_and_move(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import re

    _isolated_home(monkeypatch, tmp_path)
    try:
        added = runner.invoke(app, ["kanban", "add", "Fix the bug", "--lane", "solve"])
        assert added.exit_code == 0 and "backlog" in added.stdout
        card_id = re.search(r"added (\w+)", added.stdout).group(1)  # type: ignore[union-attr]

        board = runner.invoke(app, ["kanban", "board"])
        assert "Fix the bug" in board.stdout

        moved = runner.invoke(app, ["kanban", "move", card_id, "doing"])
        assert moved.exit_code == 0
        assert runner.invoke(app, ["kanban", "move", card_id, "nonsense"]).exit_code == 1
    finally:
        get_settings.cache_clear()


def test_memory_graph_builds_from_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated_home(monkeypatch, tmp_path)
    try:
        runner.invoke(app, ["memory", "add", "PassaPro uses Supabase"])
        out = runner.invoke(app, ["memory", "graph"])
        assert out.exit_code == 0
        assert "PassaPro" in out.stdout and "uses" in out.stdout and "Supabase" in out.stdout
    finally:
        get_settings.cache_clear()


def test_kanban_learn_turns_recurring_tasks_into_cards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated_home(monkeypatch, tmp_path)
    try:
        from chimera.evolution import ExperienceBuffer

        buf = ExperienceBuffer(tmp_path / "chimera_home" / "experience.json")
        for _ in range(3):
            buf.record("rotate the access logs", "success")

        learned = runner.invoke(app, ["kanban", "learn", "--min", "3", "--yes"])
        assert learned.exit_code == 0
        assert "added 1 card" in learned.stdout

        board = runner.invoke(app, ["kanban", "board"])
        assert "rotate-the-access" in board.stdout

        again = runner.invoke(app, ["kanban", "learn", "--min", "3", "--yes"])
        assert "added 0 card" in again.stdout  # dedup: already on the board
    finally:
        get_settings.cache_clear()


def test_cron_learn_creates_confirmed_jobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolated_home(monkeypatch, tmp_path)
    try:
        from chimera.evolution import ExperienceBuffer

        buf = ExperienceBuffer(tmp_path / "chimera_home" / "experience.json")
        buf.record("back up the database", "success")
        buf.record("back up the database", "success")  # recurs (>= 2)

        learned = runner.invoke(app, ["cron", "learn", "--min", "2", "--yes"])
        assert learned.exit_code == 0
        assert "created 1 cron" in learned.stdout

        listed = runner.invoke(app, ["cron", "list"])
        assert "back-up-the-database" in listed.stdout  # the confirmed job is now stored
    finally:
        get_settings.cache_clear()
