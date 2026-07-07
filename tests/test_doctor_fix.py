"""Tests for `doctor --fix` safe setup repairs (M15-D1)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from chimera.cli.main import _doctor_fixes


def test_creates_missing_state_dir(tmp_path: Path) -> None:
    home = tmp_path / "state"
    assert not home.exists()
    notes = _doctor_fixes(SimpleNamespace(home=home), cwd=tmp_path)
    assert home.exists()
    assert any("state dir" in n for n in notes)


def test_scaffolds_env_from_example(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("CHIMERA_OPENROUTER_KEYS=", encoding="utf-8")
    home = tmp_path / "state"
    notes = _doctor_fixes(SimpleNamespace(home=home), cwd=tmp_path)
    assert (tmp_path / ".env").exists()
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "CHIMERA_OPENROUTER_KEYS="
    assert any(".env" in n for n in notes)


def test_does_not_clobber_existing_env(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("KEY=", encoding="utf-8")
    (tmp_path / ".env").write_text("KEY=sk-existing", encoding="utf-8")
    home = tmp_path / "state"
    home.mkdir()
    notes = _doctor_fixes(SimpleNamespace(home=home), cwd=tmp_path)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "KEY=sk-existing"  # untouched
    assert notes == []  # nothing to fix


def test_never_writes_a_secret(tmp_path: Path) -> None:
    # No .env.example -> nothing is scaffolded; a missing key is never invented.
    home = tmp_path / "state"
    _doctor_fixes(SimpleNamespace(home=home), cwd=tmp_path)
    assert not (tmp_path / ".env").exists()


def test_nothing_to_fix_is_empty(tmp_path: Path) -> None:
    home = tmp_path / "state"
    home.mkdir()
    assert _doctor_fixes(SimpleNamespace(home=home), cwd=tmp_path) == []
