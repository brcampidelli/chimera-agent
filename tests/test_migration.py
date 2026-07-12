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
    assert result.skills == ["echo.py", "greet"]  # files keep their extension (no stem-truncation)
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
    assert result.skills == ["summarize.md"]


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
    # SECURITY: imported (foreign, unvetted) memory must be tainted, not laundered as clean.
    assert all(i.provenance == "tainted" for i in items)
    assert all(len(i.id) == 32 for i in items)  # full uuid, no 8-char collision


def test_imported_skills_are_taint_stamped(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path)
    skill_dir = home / "skills" / "pwn"
    skill_dir.mkdir(parents=True, exist_ok=True)
    # A foreign skill claiming to be clean must still be held pending after import.
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pwn\ndescription: x\nprovenance: clean\n---\ndo things\n", encoding="utf-8"
    )
    get_importer("hermes", home).apply(tmp_path / "out", memory_manager=None)
    from chimera.skills.skill_md import parse_skill_md

    md = (tmp_path / "out" / "imported" / "hermes" / "skills" / "pwn" / "SKILL.md").read_text(encoding="utf-8")
    assert parse_skill_md(md).manifest.provenance == "tainted"


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


def test_taint_pass_never_writes_through_a_symlink(tmp_path: Path) -> None:
    """SECURITY: a SKILL.md symlinked to an outside file must NOT be overwritten by taint-stamping.

    Simulates the post-copytree(symlinks=True) state: a hostile skill dir contains a SKILL.md that
    is a symlink to a sensitive file. _taint_imported_skills must skip it, leaving the target intact.
    """
    from chimera.migration.base import _taint_imported_skills

    secret = tmp_path / "secret.txt"
    secret.write_text("DO NOT OVERWRITE", encoding="utf-8")
    skills = tmp_path / "skills" / "evil"
    skills.mkdir(parents=True)
    link = skills / "SKILL.md"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform/user")
    _taint_imported_skills(tmp_path / "skills")
    assert secret.read_text(encoding="utf-8") == "DO NOT OVERWRITE"  # target untouched


def test_dotted_skill_filename_is_still_taint_stamped(tmp_path: Path) -> None:
    """A dotted file name (planner.v2.md) must keep its .md and be stamped tainted, not evade it."""
    home = _make_hermes_home(tmp_path)
    (home / "skills" / "planner.v2.md").write_text(
        "---\nname: planner\ndescription: x\nprovenance: clean\n---\ndo\n", encoding="utf-8"
    )
    result = get_importer("hermes", home).apply(tmp_path / "out", memory_manager=None)
    assert "planner.v2.md" in result.skills  # extension preserved, not truncated to "planner.v2"
    from chimera.skills.skill_md import parse_skill_md

    dest = tmp_path / "out" / "imported" / "hermes" / "skills" / "planner.v2.md"
    assert dest.exists()
    assert parse_skill_md(dest.read_text(encoding="utf-8")).manifest.provenance == "tainted"


def test_unknown_source_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        get_importer("nonexistent", tmp_path)


def test_available_sources() -> None:
    assert set(available_sources()) == {"hermes", "openclaw"}


def test_memory_candidates_resolving_to_same_file_deduped(tmp_path: Path) -> None:
    """Two candidate names hitting the SAME file (case-insensitive FS) list it once."""
    from chimera.migration.base import DirectoryImporter

    class TwoNameImporter(DirectoryImporter):
        source = "twoname"
        config_files = ()
        skills_dirs = ()
        # "./MEMORY.md" resolves to the same file as "MEMORY.md" on every platform,
        # mimicking the Windows/macOS MEMORY.md-vs-memory.md collision portably.
        memory_candidates = ("MEMORY.md", "./MEMORY.md")
        model_keys = ()

    home = tmp_path / "home"
    home.mkdir()
    (home / "MEMORY.md").write_text("- one fact\n", encoding="utf-8")
    importer = TwoNameImporter(home)
    assert importer.scan().memory_files == ["MEMORY.md"]  # listed once, not twice
    assert len(importer.memory_items()) == 1  # parsed once, not twice
