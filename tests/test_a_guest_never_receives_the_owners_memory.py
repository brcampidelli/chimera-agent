"""A share link's guest never receives the owner's private memory, by any road.

Adversarial review of audit item P0-08 (2026-11): the coding turn recalled from the owner's memory
whoever sent the turn, and put the records in the receipt (``done.memory_facts``). The receipt
travels three ways a guest can read: the stream of the guest's own turn, the live stream (which
replays the owner's turns too, because the bus carries every frame of the conversation) and the
stored conversation (``/guest/api/session``). Each is tested here through the guest's own routes.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

SECRET = "the owner's vault phrase is tangerine-42"


class _Agent:
    prompts: list[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # The recalled facts ride in the config's turn notes, not in the task text.
        config = kwargs.get("config") or (args[2] if len(args) > 2 else None)
        type(self).prompts.append(str(getattr(config, "turn_notes", "")))

    def run(self, task: str, **kw: Any) -> AgentResult:
        if "boom" in task:
            raise RuntimeError("internal detail")
        type(self).prompts.append(task)
        return AgentResult(
            answer="ok", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
            model="test/model",
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app
    from chimera.evolution.wiring import build_memory_manager

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    _Agent.prompts = []
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    memory = build_memory_manager(settings)
    memory.remember(SECRET, "semantic", source="user")
    return TestClient(
        build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings, memory=memory)
    )


def _frames(text: str) -> dict[str, dict[str, Any]]:
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: "):])
    return out


def _owner_turn(client: TestClient, tmp_path: Path, message: str, session: str | None = None) -> str:
    body: dict[str, Any] = {"message": message, "workspace": str(tmp_path / "ws"), "stream": False}
    if session:
        body["session_id"] = session
    text = client.post("/api/code/turn", json=body).text
    found = re.search(r'"session_id": ?"([0-9a-f]+)"', text)
    assert found, text[:400]
    return found.group(1)


@pytest.fixture(autouse=True)
def _fresh_settings() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_owner_turn_does_recall_the_fact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The precondition: the harness really recalls the fact, so the guest tests below prove something."""
    client = _client(tmp_path, monkeypatch)
    body = {"message": "what is the vault phrase tangerine", "workspace": str(tmp_path / "ws"), "stream": False}
    done = _frames(client.post("/api/code/turn", json=body).text)["done"]
    assert [row["content"] for row in done["memory_facts"]] == [SECRET]
    assert any(SECRET in note for note in _Agent.prompts)


def test_a_guest_turn_recalls_nothing_and_its_stream_carries_no_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _owner_turn(client, tmp_path, "hello there")
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    before = len(_Agent.prompts)

    spoke = client.post(
        "/guest/api/turn",
        json={"message": "what is the vault phrase tangerine", "name": "Ana"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert spoke.status_code == 200
    assert "tangerine-42" not in spoke.text
    assert _frames(spoke.text)["done"]["memory_facts"] == []
    assert _frames(spoke.text)["done"]["memory_facts_used"] == 0
    assert all("tangerine-42" not in p for p in _Agent.prompts[before:])


def test_the_guest_cannot_read_an_owner_turn_recalled_facts_in_the_stored_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _owner_turn(client, tmp_path, "what is the vault phrase tangerine")
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]

    owner_view = client.get(f"/api/code/sessions/{sid}").json()
    assert SECRET in json.dumps(owner_view), "the owner's own view should still list the fact"
    guest_view = client.get("/guest/api/session", headers={"Authorization": f"Bearer {token}"})
    assert guest_view.status_code == 200
    assert "tangerine-42" not in guest_view.text
    done = guest_view.json()["exchanges"][0]["done"]
    assert "memory_facts" not in done and "memory_facts_used" in done  # only the records go


def test_the_guest_following_an_owner_turn_on_the_live_stream_sees_no_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real server, because a test client buffers an endless stream and would never return."""
    import threading

    import httpx

    import chimera.core
    from chimera.api import build_api_app
    from chimera.evolution.wiring import build_memory_manager
    from tests.test_an_open_guest_stream_closes_when_its_link_does import _serve

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    for key in ("CHIMERA_SHARING", "CHIMERA_SHARE_EXPIRY_HOURS", "CHIMERA_SERVER_TOKEN"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND="json")
    memory = build_memory_manager(settings)
    memory.remember(SECRET, "semantic", source="user")
    app = build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings, memory=memory)
    base, server, thread = _serve(app)
    lines: list[str] = []
    try:
        owner = httpx.Client(base_url=base, timeout=20)
        body = {"message": "what is the vault phrase tangerine", "workspace": str(ws), "stream": False}
        text = owner.post("/api/code/turn", json=body).text
        found = re.search(r'"session_id": ?"([0-9a-f]+)"', text)
        assert found, text[:300]
        sid = found.group(1)
        assert SECRET in json.dumps(app.state.session_bus.replay(sid)), "the bus frame holds the records"
        token = owner.post(f"/api/code/sessions/{sid}/share").json()["token"]

        def read() -> None:
            try:
                with httpx.stream(
                    "GET", f"{base}/guest/api/live", params={"t": token, "name": "Ana"},
                    timeout=httpx.Timeout(20, read=20),
                ) as response:
                    for line in response.iter_lines():
                        lines.append(line)
            except httpx.HTTPError:
                pass

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        for _ in range(100):
            if any('"event": "done"' in line for line in lines):
                break
            time.sleep(0.1)
    finally:
        app.state.session_bus.end_guests()
        app.state.guest_server.stop()
        server.should_exit = True
        thread.join(timeout=5)
        get_settings.cache_clear()
    joined = " ".join(lines)
    assert '"event": "done"' in joined, "the replay never reached the owner's done frame"
    assert "tangerine-42" not in joined
    assert "\"memory_facts\"" not in joined  # the records; the count (memory_facts_used) may stay


def test_a_failed_guest_turn_does_not_name_the_owners_log_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generic failure sentence ends with the log's path; the guest sees the error frame."""
    import chimera.file_log

    log = tmp_path / "owner-private-dir" / "chimera.log"
    monkeypatch.setattr(chimera.file_log, "file_log_path", lambda: log)
    client = _client(tmp_path, monkeypatch)
    sid = _owner_turn(client, tmp_path, "hello there")
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]

    owner = client.post(
        "/api/code/turn",
        json={"message": "boom", "workspace": str(tmp_path / "ws"), "stream": False, "session_id": sid},
    )
    assert "owner-private-dir" in owner.text, "the owner's own error still names the log"
    guest = client.post(
        "/guest/api/turn", json={"message": "boom", "name": "Ana"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert "the coding turn failed" in guest.text
    assert "owner-private-dir" not in guest.text
