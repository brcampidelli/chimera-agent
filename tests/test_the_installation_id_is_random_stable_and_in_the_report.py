"""The installation id (C7): random, made once, kept in the app's data, and in every report.

A support label, not a fingerprint: two installs on the same machine get different ids, so nothing
about the machine or the person goes into it. Stable, because a label that changes between two
reports labels nothing — and a malformed file is replaced, never handed out.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.install_id import installation_id


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", "")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browsers"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_id_is_made_once_and_read_back(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = installation_id(home)

    assert len(first) == 32 and first.isalnum() and first == first.lower()
    assert (home / ".installation-id").read_text(encoding="ascii").strip() == first
    assert installation_id(home) == first


def test_two_installs_on_one_machine_get_different_ids(tmp_path: Path) -> None:
    assert installation_id(tmp_path / "a") != installation_id(tmp_path / "b")


@pytest.mark.parametrize("garbage", ["", "short", "X" * 32, "a" * 31 + "!", "a" * 40])
def test_a_malformed_id_is_replaced_not_handed_out(garbage: str, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".installation-id").write_text(garbage, encoding="ascii")

    fresh = installation_id(home)

    assert fresh != garbage and len(fresh) == 32
    assert installation_id(home) == fresh, "the replacement must be the id from now on"


def test_the_diagnostics_carry_the_same_id_in_the_field_and_the_copied_text(tmp_path: Path) -> None:
    from chimera.api import build_api_app

    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")  # type: ignore[call-arg]
    client = TestClient(build_api_app(lambda: None, workspace=ws, settings=settings))  # type: ignore[arg-type]

    first = client.get("/api/diagnostics").json()
    second = client.get("/api/diagnostics").json()

    assert first["install_id"] == second["install_id"] == installation_id(home)
    assert f"installation id: {first['install_id']}" in first["report"]
