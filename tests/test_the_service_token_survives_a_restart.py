"""The service's token is the owner's and it is persistent (S1, decision D4).

A token that changed on every restart would lock out every client holding the old one — the
desktop shell, a script, a paired phone later — after a routine ``chimera service restart``. So it
lives in ``<home>/service.token`` (0600, created once), and an owner-chosen
``CHIMERA_SERVER_TOKEN`` wins over it. A token changed on the Settings screen must reach all three
places a running service keeps it — its environment (which outranks ``.env``), the file and the
record — or the screen would say "saved" while clients kept being refused, or let in, by the old.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from chimera.config import get_settings
from chimera.core.service_record import load_or_create_token, read_record, token_path
from chimera.core.service_watch import ServicePublication, service_token
from tests.service_harness import ServiceSandbox, wait_dead


@pytest.fixture
def no_token(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.delenv("CHIMERA_SERVER_TOKEN")  # monkeypatch restores whatever was there
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_restart_keeps_the_token_and_replaces_the_process(tmp_path: Path) -> None:
    box = ServiceSandbox(tmp_path)
    try:
        box.start()
        before = box.record()
        assert before is not None, box.log()

        restarted = box.cli("restart", "--timeout", "120")
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr + box.log()
        after = box.record()

        assert after is not None and after.pid != before.pid and after.id != before.id
        assert wait_dead(before.pid), "the old process survived its restart"
        assert after.token == before.token, "restart rotated the owner's token"
        assert token_path(box.home).read_text(encoding="utf-8").strip() == after.token
    finally:
        box.close()


def test_the_token_is_created_once(tmp_path: Path) -> None:
    first = load_or_create_token(tmp_path)
    assert len(first) >= 32
    assert load_or_create_token(tmp_path) == first


def test_an_owner_chosen_token_wins_and_none_is_written(tmp_path: Path, no_token: None) -> None:
    assert service_token("the-owners", tmp_path) == "the-owners"
    assert not token_path(tmp_path).exists()
    assert "CHIMERA_SERVER_TOKEN" not in os.environ


def test_without_one_the_folders_token_is_put_where_the_guard_reads_it(tmp_path: Path, no_token: None) -> None:
    token = service_token(None, tmp_path)
    assert token == load_or_create_token(tmp_path)
    assert get_settings().server_token == token


def test_a_token_saved_from_settings_reaches_environment_file_and_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_token: None
) -> None:
    monkeypatch.setenv("CHIMERA_SERVICE_DIR", str(tmp_path / "svc"))
    monkeypatch.setenv("CHIMERA_SERVICE_CHANNEL", "dev")
    publication = ServicePublication.start(
        url="http://127.0.0.1:1", token=service_token(None, tmp_path), home=tmp_path, on_step_down=lambda: None
    )
    try:
        publication.token_changed("a-new-one")
        assert get_settings().server_token == "a-new-one"
        assert token_path(tmp_path).read_text(encoding="utf-8") == "a-new-one"
        assert read_record(tmp_path / "svc" / "service-dev.json").token == "a-new-one"  # type: ignore[union-attr]

        publication.token_changed("")  # cleared: never served without one
        assert get_settings().server_token
    finally:
        publication.close()


def test_the_settings_route_hands_a_new_token_to_the_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_token: None
) -> None:
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.interface import ChatSession

    monkeypatch.chdir(tmp_path)  # `PATCH /api/config` writes the `.env` of the working directory
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "0")
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "")  # the save below writes it: own the name
    monkeypatch.delenv("CHIMERA_DEFAULT_MODEL")
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND="json")  # type: ignore[call-arg]
    api = build_api_app(lambda: ChatSession(None), workspace=tmp_path, settings=settings)  # type: ignore[arg-type]  # no turn runs
    handed: list[str] = []
    api.state.on_server_token_changed = handed.append

    with TestClient(api) as client:
        assert client.patch("/api/config", json={"CHIMERA_DEFAULT_MODEL": "x/y"}).status_code == 200
        assert handed == [], "a save that did not touch the token reached the service"
        assert client.patch("/api/config", json={"CHIMERA_SERVER_TOKEN": "fresh"}).status_code == 200

    assert handed == ["fresh"]
