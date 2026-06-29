"""Tests for migration importers (Hermes/OpenClaw) using fixture homes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.migration import available_sources, get_importer


def _make_hermes_home(root: Path) -> Path:
    home = root / "hermes_home"
    (home / "skills" / "greet").mkdir(parents=True)
    (home / "skills" / "greet" / "SKILL.md").write_text("# Greet\nSays hi.", encoding="utf-8")
    (home / "skills" / "echo.py").write_text("# echo skill\n", encoding="utf-8")
    (home / "config.yaml").write_text(
        "model:\n  default: openrouter/anthropic/claude-opus-4-8\n", encoding="utf-8"
    )
    (home / "MEMORY.md").write_text("- remembered fact\n", encoding="utf-8")
    return home


def test_hermes_scan(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path)
    result = get_importer("hermes", home).scan()

    assert result.source == "hermes"
    assert result.dry_run is True
    assert result.default_model == "openrouter/anthropic/claude-opus-4-8"
    assert result.skills == ["echo", "greet"]
    assert "MEMORY.md" in result.memory_files
    assert any("memory" in note.lower() for note in result.notes)


def test_hermes_apply_writes_artifacts(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path)
    target = tmp_path / "chimera_home"
    result = get_importer("hermes", home).apply(target)

    assert result.dry_run is False
    config_path = target / "imported" / "hermes" / "config.json"
    assert config_path.exists()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model"]["default"] == "openrouter/anthropic/claude-opus-4-8"

    assert (target / "imported" / "hermes" / "skills" / "greet" / "SKILL.md").exists()
    assert (target / "imported" / "hermes" / "skills" / "echo.py").exists()


def test_openclaw_scan_json_config(tmp_path: Path) -> None:
    home = tmp_path / "openclaw_home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"defaultModel": "openai/gpt-5.5"}), encoding="utf-8")
    (home / "skills").mkdir()
    (home / "skills" / "summarize.md").write_text("# Summarize", encoding="utf-8")

    result = get_importer("openclaw", home).scan()
    assert result.default_model == "openai/gpt-5.5"
    assert result.skills == ["summarize"]


def test_memory_items_parsed(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path)
    (home / "MEMORY.md").write_text(
        "# Index\n- fact one\n- fact two\n\nplain fact\n", encoding="utf-8"
    )
    items = get_importer("hermes", home).memory_items()
    contents = [i.content for i in items]
    assert "fact one" in contents
    assert "fact two" in contents
    assert "plain fact" in contents
    assert all(i.source == "hermes" for i in items)


def test_apply_merges_memory(tmp_path: Path) -> None:
    from chimera.memory import MemoryManager, MemoryStore

    home = _make_hermes_home(tmp_path)
    (home / "MEMORY.md").write_text("- alpha\n- beta\n", encoding="utf-8")
    manager = MemoryManager(MemoryStore(tmp_path / "mem.json"))

    result = get_importer("hermes", home).apply(tmp_path / "out", memory_manager=manager)
    assert result.memory_merged == {"ADD": 2, "UPDATE": 0, "NOOP": 0}
    assert len(manager.store) == 2

    # re-applying merges again -> all NOOP (non-destructive, deduped)
    result2 = get_importer("hermes", home).apply(tmp_path / "out", memory_manager=manager)
    assert result2.memory_merged == {"ADD": 0, "UPDATE": 0, "NOOP": 2}
    assert len(manager.store) == 2


def test_unknown_source_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        get_importer("nonexistent", tmp_path)


def test_available_sources() -> None:
    assert set(available_sources()) == {"hermes", "openclaw"}
