"""A coding conversation can be shared with a second person, by a token that reaches only it.

Item 3 of the list audited on 2026-09-16 ("multiplayer"). The owner's decision on 2026-09-17: a
token per shared session, never the server token. A share token opens one conversation — read it,
watch it live, send a message into it, see who is there — and nothing else; revoking it closes
that one door. The guest app is the only thing the network listener serves, so what the LAN can
reach is the four guest routes and nothing the owner's token protects.

What is pinned: the store mints, resolves in constant time, revokes and persists; the bus numbers
frames per session, replays from a point, keeps the browser picture out of the ring, delivers to
subscribers across loops and lists them as presence; through the app a shared conversation is
readable and writable with the token and with nothing else, a guest's turn is recorded with the
guest's name on its receipt and seen by the owner, the guest app answers no owner route, deleting
the conversation revokes its tokens, a guest turn runs under the request's default seams, the
owner's live window and the guest's replay the same frames, and the network door opens a real
listener that serves the guest app and closes again.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.guest_api import ANONYMOUS, GuestServer, build_guest_app, live_frames
from chimera.api.sharing import SessionBus, ShareStore
from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

# ------------------------------------------------------------------ the store


def test_the_store_mints_resolves_revokes_and_persists(tmp_path: Path) -> None:
    store = ShareStore(tmp_path / "home" / "code_shares.json")
    a = store.mint("sess-a", label="Ana")
    b = store.mint("sess-a")
    c = store.mint("sess-b")
    assert len({a.token, b.token, c.token}) == 3 and len(a.token) >= 32
    assert store.resolve(a.token) == a and store.resolve(c.token) == c
    assert store.resolve("") is None and store.resolve("not-a-token") is None
    assert [s.token for s in store.for_session("sess-a")] == [a.token, b.token]

    # Persisted: a second store over the same file sees the same tokens.
    again = ShareStore(tmp_path / "home" / "code_shares.json")
    assert again.resolve(b.token) is not None and again.resolve(b.token).label == ""

    assert store.revoke(a.token) is True and store.revoke(a.token) is False
    assert store.resolve(a.token) is None and store.resolve(b.token) is not None
    assert store.revoke_session("sess-a") == 1 and store.for_session("sess-a") == []
    assert store.resolve(c.token) is not None  # another conversation's door is not touched
    assert ShareStore(tmp_path / "home" / "code_shares.json").resolve(c.token) is not None


def test_an_unreadable_store_file_starts_empty_rather_than_failing(tmp_path: Path) -> None:
    path = tmp_path / "code_shares.json"
    path.write_text("{not json", encoding="utf-8")
    store = ShareStore(path)
    assert store.for_session("x") == []
    minted = store.mint("x")
    assert ShareStore(path).resolve(minted.token) is not None


# ------------------------------------------------------------------ the bus


def test_the_bus_numbers_per_session_replays_from_a_point_and_keeps_pictures_out() -> None:
    bus = SessionBus(ring=3)
    first = bus.publish("s1", "turn_started", {"message": "hi"}, turn_id="t1", author="Ana")
    bus.publish("s2", "turn_started", {"message": "other"})
    bus.publish("s1", "browser", {"jpeg": "..."}, turn_id="t1", keep=False)
    bus.publish("s1", "token", {"text": "a"}, turn_id="t1")
    bus.publish("s1", "done", {"answer": "ok"}, turn_id="t1")
    assert first["session_seq"] == 1 and first["author"] == "Ana" and first["turn_id"] == "t1"
    # The picture took a number and is not in the ring.
    assert [f["session_seq"] for f in bus.replay("s1")] == [1, 3, 4]
    assert [f["event"] for f in bus.replay("s1", since=1)] == ["token", "done"]
    assert bus.replay("s2") and bus.replay("s2")[0]["session_seq"] == 1
    assert bus.replay("nobody") == [] and bus.seq("nobody") == 0
    # The ring is bounded: one more frame and the oldest kept one is gone.
    bus.publish("s1", "extra", {}, turn_id="t1")
    assert [f["session_seq"] for f in bus.replay("s1")] == [3, 4, 5]


def test_subscribers_get_frames_published_from_another_thread_and_are_the_presence() -> None:
    bus = SessionBus()

    async def scenario() -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        sub = bus.subscribe("s1", name="Ana", loop=loop)
        assert bus.presence("s1") == ["Ana"]
        got = [await asyncio.wait_for(sub.queue.get(), timeout=2)]  # its own arrival
        threading.Thread(target=lambda: bus.publish("s1", "token", {"text": "x"}, turn_id="t")).start()
        got.append(await asyncio.wait_for(sub.queue.get(), timeout=2))
        other = bus.subscribe("s1", name="Bia", loop=loop)
        got.append(await asyncio.wait_for(sub.queue.get(), timeout=2))  # Bia's arrival
        assert sorted(bus.presence("s1")) == ["Ana", "Bia"]
        bus.unsubscribe("s1", other.id)
        got.append(await asyncio.wait_for(sub.queue.get(), timeout=2))  # Bia's leaving
        bus.unsubscribe("s1", sub.id)
        assert bus.presence("s1") == []
        return got

    frames = asyncio.run(scenario())
    # A viewer's first frame is the presence list with themselves on it: who is here, now.
    assert frames[0]["event"] == "presence" and frames[0]["payload"]["names"] == ["Ana"]
    assert frames[1]["event"] == "token" and frames[1]["payload"] == {"text": "x"}
    assert frames[2]["event"] == "presence" and sorted(frames[2]["payload"]["names"]) == ["Ana", "Bia"]
    assert frames[3]["event"] == "presence" and frames[3]["payload"]["names"] == ["Ana"]
    # Presence is about now: never in the ring.
    assert [f["event"] for f in bus.replay("s1")] == ["token"]


def test_the_live_window_replays_then_follows_and_leaves_the_presence_when_it_ends() -> None:
    bus = SessionBus()
    bus.publish("s1", "turn_started", {"message": "before"}, turn_id="t0")
    bus.publish("s1", "done", {"answer": "old"}, turn_id="t0")

    async def scenario() -> list[dict[str, str]]:
        seen: list[dict[str, str]] = []
        gone = False

        async def disconnected() -> bool:
            return gone

        stream = live_frames(bus, "s1", since=1, name="Ana", disconnected=disconnected, heartbeat_seconds=0.05)
        seen.append(await stream.__anext__())  # the replay: only what came after seq 1
        assert bus.presence("s1") == ["Ana"]
        seen.append(await stream.__anext__())  # the viewer's own arrival
        seen.append(await stream.__anext__())  # a heartbeat, nothing published for 50 ms
        threading.Thread(target=lambda: bus.publish("s1", "token", {"text": "live"}, turn_id="t1")).start()
        seen.append(await stream.__anext__())
        gone = True
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
        assert bus.presence("s1") == []
        return seen

    seen = asyncio.run(scenario())
    assert seen[0]["event"] == "done" and json.loads(seen[0]["data"])["session_seq"] == 2
    assert seen[1]["event"] == "presence" and json.loads(seen[1]["data"])["payload"]["names"] == ["Ana"]
    assert seen[2]["event"] == "heartbeat"
    assert seen[3]["event"] == "token" and json.loads(seen[3]["data"])["payload"] == {"text": "live"}


# ------------------------------------------------------------------ through the app


class _Agent:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def run(self, task: str, **kw: Any) -> AgentResult:
        history = list(kw.get("history") or [])
        return AgentResult(
            answer=f"answered: {task}",
            steps=1,
            stopped_reason="final",
            transcript=[*history, {"role": "user", "content": task}, {"role": "assistant", "content": f"answered: {task}"}],
            model="test/model",
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home))
    return TestClient(build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings))


def _frames(text: str) -> dict[str, dict[str, Any]]:
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


def _session_of(client: TestClient, message: str) -> str:
    response = client.post("/api/code/turn", json={"message": message})
    assert response.status_code == 200
    return str(_frames(response.text)["session"]["session_id"])


def test_a_shared_conversation_is_readable_and_writable_with_the_token_and_with_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _session_of(client, "fix the login page")
    other = _session_of(client, "a different conversation")

    assert client.post("/api/code/sessions/nope/share").status_code == 404
    minted = client.post(f"/api/code/sessions/{sid}/share", json={"label": "Ana"}).json()
    token = minted["token"]
    assert minted["session_id"] == sid and minted["label"] == "Ana"
    assert minted["url"] is None  # the network door is closed: no link that goes nowhere

    # Nothing without the token, nothing with a wrong one.
    assert client.get("/guest/api/session").status_code == 401
    assert client.get("/guest/api/session", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/guest/api/session", params={"t": "wrong"}).status_code == 401

    bearer = {"Authorization": f"Bearer {token}"}
    view = client.get("/guest/api/session", headers=bearer).json()
    assert view["session_id"] == sid
    assert view["workspace_name"] == "ws"  # the folder's name, never the owner's path
    assert str(tmp_path / "ws") != "ws" and str(tmp_path) not in json.dumps(view)
    assert [e["you"] for e in view["exchanges"]] == ["fix the login page"]
    assert view["exchanges"][0]["done"] is not None and "author" not in view["exchanges"][0]["done"]
    assert view["presence"] == []
    # The query form too — the live stream cannot send a header.
    assert client.get("/guest/api/session", params={"t": token}).status_code == 200

    # A guest's message runs as a turn, on THIS conversation, under the guest's name.
    spoke = client.post("/guest/api/turn", json={"message": "and the logout", "name": "  Ana   Lima "}, headers=bearer)
    assert spoke.status_code == 200
    done = _frames(spoke.text)["done"]
    assert done["answer"] == "answered: and the logout"

    owner_view = client.get(f"/api/code/sessions/{sid}").json()
    assert [e["you"] for e in owner_view["exchanges"]] == ["fix the login page", "and the logout"]
    assert owner_view["exchanges"][1]["done"]["author"] == "Ana Lima"
    assert "author" not in owner_view["exchanges"][0]["done"]
    # The other conversation is untouched, and unreachable: the guest app has no way to name it.
    assert [e["you"] for e in client.get(f"/api/code/sessions/{other}").json()["exchanges"]] == ["a different conversation"]
    assert client.get(f"/guest/api/sessions/{other}", headers=bearer).status_code == 404
    assert client.get("/guest/api/sessions", headers=bearer).status_code == 404

    # An empty message is refused before anything is built.
    assert client.post("/guest/api/turn", json={"message": "   "}, headers=bearer).status_code == 400


def test_the_guest_app_answers_no_owner_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _session_of(client, "hello")
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    for path in (
        "/guest/api/config", "/guest/api/doctor", "/guest/api/code/sessions", "/guest/api/health",
        f"/guest/api/code/sessions/{sid}", "/guest/api/code/turn", "/guest/api/code/approve",
        "/guest/api/code/share/network", f"/guest/api/code/sessions/{sid}/share",
    ):
        got = client.get(path, headers=bearer)
        assert got.status_code in (404, 405), (path, got.status_code)
        got = client.post(path, headers=bearer, json={})
        assert got.status_code in (404, 405), (path, got.status_code)


def test_the_bus_carries_the_guests_turn_to_the_owner_with_the_author_on_every_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _session_of(client, "hello")
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    client.post("/guest/api/turn", json={"message": "from the guest", "name": "Ana"}, headers={"Authorization": f"Bearer {token}"})

    bus: SessionBus = client.app.state.session_bus  # type: ignore[attr-defined]
    frames = bus.replay(sid)
    started = [f for f in frames if f["event"] == "turn_started"]
    assert [f["payload"]["message"] for f in started] == ["hello", "from the guest"]
    assert [f["author"] for f in started] == ["", "Ana"]
    guest_turn = started[1]["turn_id"]
    guest_frames = [f for f in frames if f["turn_id"] == guest_turn]
    assert all(f["author"] == "Ana" for f in guest_frames)
    assert guest_frames[-1]["event"] == "done" and guest_frames[-1]["payload"]["answer"] == "answered: from the guest"
    # Numbered by the session, without gaps, whoever started the turn.
    seqs = [f["session_seq"] for f in frames]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # And the guest reads the same frames the owner would.
    guest_view = client.get("/guest/api/session", headers={"Authorization": f"Bearer {token}"}).json()
    assert guest_view["seq"] == bus.seq(sid) and guest_view["exchanges"][1]["done"]["author"] == "Ana"


def test_deleting_the_conversation_revokes_its_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _session_of(client, "hello")
    a = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    b = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    assert len(client.get(f"/api/code/sessions/{sid}/shares").json()["shares"]) == 2

    # One revoked alone; the other still opens.
    assert client.delete(f"/api/code/sessions/{sid}/shares/{a}").json() == {"ok": True}
    assert client.get("/guest/api/session", headers={"Authorization": f"Bearer {a}"}).status_code == 401
    assert client.get("/guest/api/session", headers={"Authorization": f"Bearer {b}"}).status_code == 200
    # A token of another conversation cannot be revoked through this one's route.
    other = _session_of(client, "other")
    c = client.post(f"/api/code/sessions/{other}/share").json()["token"]
    assert client.delete(f"/api/code/sessions/{sid}/shares/{c}").json() == {"ok": False}

    assert client.delete(f"/api/code/sessions/{sid}").json() == {"ok": True}
    assert client.get("/guest/api/session", headers={"Authorization": f"Bearer {b}"}).status_code == 401
    assert client.get(f"/api/code/sessions/{sid}/shares").json()["shares"] == []
    assert client.get("/guest/api/session", headers={"Authorization": f"Bearer {c}"}).status_code == 200


def test_a_guest_turn_runs_under_the_request_defaults_on_the_sessions_own_folder(tmp_path: Path) -> None:
    """The seams a guest gets are the cautious defaults, whatever the owner's screen has chosen —
    and the folder is the conversation's, not something the guest can name."""
    from chimera.api.code_api import CodeTurnRequest

    seen: list[tuple[CodeTurnRequest, str]] = []

    async def start_turn(req: CodeTurnRequest, *, author: str = "") -> dict[str, str]:
        seen.append((req, author))
        return {"started": "yes"}

    store = ShareStore(tmp_path / "shares.json")
    share = store.mint("sess-1")
    guest = build_guest_app(
        store=store,
        bus=SessionBus(),
        session_view=lambda sid: {"id": sid, "workspace": str(tmp_path / "proj"), "exchanges": []},
        session_workspace=lambda sid: str(tmp_path / "proj"),
        start_turn=start_turn,
    )
    client = TestClient(guest)
    assert client.post("/api/turn", json={"message": "do it", "name": ""}, headers={"Authorization": f"Bearer {share.token}"}).status_code == 200
    req, author = seen[0]
    assert author == ANONYMOUS
    assert req.session_id == "sess-1" and req.workspace == str(tmp_path / "proj")
    assert req.message == "do it"
    defaults = CodeTurnRequest(message="x")
    assert req.allow_host_exec == defaults.allow_host_exec and req.posture == defaults.posture
    assert req.allow_tools == defaults.allow_tools and req.deny_tools == defaults.deny_tools
    assert req.provider == defaults.provider and req.model is None and req.fuse == defaults.fuse
    assert req.max_usd == defaults.max_usd
    # A name is cleaned and capped, never a paragraph in the presence list.
    client.post("/api/turn", json={"message": "again", "name": "x" * 200}, headers={"Authorization": f"Bearer {share.token}"})
    assert len(seen[1][1]) == 40


def test_the_network_door_opens_a_real_listener_that_serves_only_the_guest_app_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _session_of(client, "hello")
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    assert client.get("/api/code/share/network").json() == {"open": False, "port": None, "urls": []}

    opened = client.post("/api/code/share/network", json={"port": 0}).json()
    door: GuestServer = client.app.state.guest_server  # type: ignore[attr-defined]
    try:
        assert opened["open"] is True and opened["port"] == door.port and door.port
        port = int(opened["port"])
        base = f"http://127.0.0.1:{port}"

        def fetch(path: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
            request = urllib.request.Request(base + path, headers=headers or {})
            for _ in range(50):
                try:
                    with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
                        return response.status, response.read().decode("utf-8")
                except urllib.error.HTTPError as exc:
                    return exc.code, ""
                except (urllib.error.URLError, ConnectionError, OSError):
                    import time

                    time.sleep(0.05)
            raise AssertionError("the listener never answered")

        status, body = fetch("/api/session", {"Authorization": f"Bearer {token}"})
        assert status == 200 and json.loads(body)["session_id"] == sid
        assert fetch("/api/session")[0] == 401
        # The owner's routes are not on this listener at all.
        assert fetch("/api/code/sessions", {"Authorization": f"Bearer {token}"})[0] == 404
        assert fetch("/api/config")[0] == 404
        assert fetch("/api/health")[0] == 404
        # The link the owner hands out now exists, on this port, carrying the token.
        share = client.post(f"/api/code/sessions/{sid}/share").json()
        assert share["url"] is None or (f":{port}/?t={share['token']}" in share["url"])
        # Opening again is the same door.
        assert client.post("/api/code/share/network", json={"port": 0}).json()["port"] == port
    finally:
        closed = client.delete("/api/code/share/network").json()
    assert closed == {"open": False, "port": None, "urls": []}
    assert door.open is False
    with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/session", timeout=1)  # noqa: S310
