"""A guest's live stream that is already open closes when its link stops opening the conversation.

Found by an adversarial review of study 29, P5.5, against a real server: a guest opened
``/guest/api/live``, the owner pressed "Revoke all" and switched sharing off, a fresh request from
the guest answered 401 — and a frame published afterwards still reached the guest's open stream,
through the owner's ``/guest`` mount and through the LAN door alike. ``share_of`` admitted the
stream once, when it connected, and nothing asked again; an expiry never reached a guest who kept
the tab open, because heartbeats every fifteen seconds kept the connection alive forever. The card
meanwhile said "Off. No link opens".

What is pinned here:

* a stream asks whether its link still opens before every frame — a replayed one, a live one and a
  heartbeat — and ends the first time the answer is no (revoked, expired, sharing off);
* a revoke from any route ends the streams the removed links opened at once, without waiting for
  a frame to be refused, and leaves the owner's own window and other links' streams alone;
* through the app, on a real server, a revoke and sharing switched off each end an open guest
  stream on the owner's mount and on the LAN door, and the frame published after reaches nobody.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time as real_time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import chimera.api.sharing as sharing
from chimera.api.guest_api import live_frames
from chimera.api.sharing import SessionBus, ShareStore
from chimera.config import get_settings
from chimera.interface import ChatSession
from tests.test_a_conversation_can_be_shared_by_a_token_that_reaches_only_it import _Agent

SECRET = "SECRET-AFTER-THE-LINK-CLOSED"


async def _never() -> bool:
    return False


async def _next(stream: AsyncIterator[dict[str, str]], within: float = 2.0) -> dict[str, str]:
    return await asyncio.wait_for(stream.__anext__(), timeout=within)


async def _ends(stream: AsyncIterator[dict[str, str]], within: float = 2.0) -> None:
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(stream.__anext__(), timeout=within)


def _publish_from_a_thread(bus: SessionBus, session_id: str, text: str) -> None:
    # A turn publishes from its worker thread; so does this.
    worker = threading.Thread(target=lambda: bus.publish(session_id, "token", {"text": text}))
    worker.start()
    worker.join()


# ------------------------------------------------------------------ the stream re-asks


def test_a_revoked_link_delivers_no_frame_published_after_the_revoke(tmp_path: Path) -> None:
    store = ShareStore(tmp_path / "code_shares.json")
    bus = SessionBus()
    share = store.mint("s1")

    async def scenario() -> None:
        # No revoke listener here: what is pinned is that the stream ASKS, so a revoke made by a
        # path nobody wired a listener to still delivers nothing.
        stream = live_frames(
            bus, "s1", since=0, name="Ana", disconnected=_never, heartbeat_seconds=30,
            still_open=lambda: store.resolve(share.token) is not None, share_id=share.id,
        )
        assert (await _next(stream))["event"] == "presence"  # the guest's own arrival
        _publish_from_a_thread(bus, "s1", "before")
        assert json.loads((await _next(stream))["data"])["payload"] == {"text": "before"}
        assert store.revoke(share.token) is True
        _publish_from_a_thread(bus, "s1", SECRET)
        await _ends(stream)
        assert bus.presence("s1") == []  # gone from the list the moment it ended

    asyncio.run(scenario())


def test_an_idle_stream_ends_at_its_links_expiry_instead_of_living_on_heartbeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [real_time.time()]
    monkeypatch.setattr(
        sharing, "time", SimpleNamespace(time=lambda: now[0], monotonic=real_time.monotonic)
    )
    store = ShareStore(tmp_path / "code_shares.json")
    bus = SessionBus()
    share = store.mint("s1", expires_in=3600)

    async def scenario() -> None:
        stream = live_frames(
            bus, "s1", since=0, name="Ana", disconnected=_never, heartbeat_seconds=0.05,
            still_open=lambda: store.resolve(share.token) is not None, share_id=share.id,
        )
        assert (await _next(stream))["event"] == "presence"
        assert (await _next(stream))["event"] == "heartbeat"  # nothing published: kept alive
        now[0] += 3601  # nothing is published, the hour passes
        await _ends(stream)

    asyncio.run(scenario())


def test_sharing_switched_off_ends_a_stream_and_replays_nothing_to_one_that_connects(
    tmp_path: Path,
) -> None:
    store = ShareStore(tmp_path / "code_shares.json")
    bus = SessionBus()
    share = store.mint("s1")
    bus.publish("s1", "turn_started", {"message": "earlier"}, turn_id="t0")
    on = [True]

    def still_open() -> bool:
        return on[0] and store.resolve(share.token) is not None

    async def scenario() -> None:
        stream = live_frames(
            bus, "s1", since=0, name="Ana", disconnected=_never, heartbeat_seconds=30,
            still_open=still_open, share_id=share.id,
        )
        assert (await _next(stream))["event"] == "turn_started"  # the replay, while on
        assert (await _next(stream))["event"] == "presence"
        on[0] = False
        _publish_from_a_thread(bus, "s1", SECRET)
        await _ends(stream)

        # The replay is asked about too: a ring full of the conversation is not handed out first.
        late = live_frames(
            bus, "s1", since=0, name="Bia", disconnected=_never, heartbeat_seconds=30,
            still_open=still_open, share_id=share.id,
        )
        await _ends(late)

    asyncio.run(scenario())


def test_a_revoke_ends_that_links_streams_at_once_and_no_one_elses(tmp_path: Path) -> None:
    store = ShareStore(tmp_path / "code_shares.json")
    bus = SessionBus()
    store.on_revoke(lambda gone: bus.end_guests({s.id for s in gone}))
    ana, bia = store.mint("s1", label="Ana"), store.mint("s1", label="Bia")

    def stream(name: str, share_id: str, token: str | None) -> AsyncIterator[dict[str, str]]:
        def still_open() -> bool:
            return token is None or store.resolve(token) is not None

        # A heartbeat a minute away: the stream must end because it was told to, not because the
        # next heartbeat happened to ask.
        return live_frames(
            bus, "s1", since=0, name=name, disconnected=_never, heartbeat_seconds=60,
            still_open=still_open, share_id=share_id,
        )

    async def scenario() -> None:
        owner = stream("owner", "", None)
        a = stream("Ana", ana.id, ana.token)
        b = stream("Bia", bia.id, bia.token)
        for s in (owner, a, b):
            assert (await _next(s))["event"] == "presence"
        # Each stream sees the arrivals after its own; drain them.
        assert (await _next(owner))["event"] == "presence" and (await _next(owner))["event"] == "presence"
        assert (await _next(a))["event"] == "presence"

        store.revoke_id(ana.id)
        await _ends(a, within=1.0)
        # Bia's stream and the owner's are still there, and still get the conversation.
        _publish_from_a_thread(bus, "s1", "after")
        seen_b = [await _next(b), await _next(b)]
        assert [f["event"] for f in seen_b] == ["presence", "token"]
        seen_o = [await _next(owner), await _next(owner)]
        assert [f["event"] for f in seen_o] == ["presence", "token"]

        # Every guest at once (sharing off) still leaves the owner's window open.
        assert bus.end_guests() == 1
        await _ends(b, within=1.0)
        assert (await _next(owner))["event"] == "presence"
        assert bus.presence("s1") == ["owner"]
        await owner.aclose()

    asyncio.run(scenario())


# ------------------------------------------------------------------ through the app, on a real server


def _serve(app: Any) -> tuple[str, Any, threading.Thread]:
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = int(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/api/health", timeout=1)
            break
        except httpx.HTTPError:
            real_time.sleep(0.05)
    return base, server, thread


class _Reader:
    """A guest holding ``/api/live`` open in a thread, as a browser tab does."""

    def __init__(self, url: str, token: str) -> None:
        self.status: int | None = None
        self.lines: list[str] = []
        self.ended = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(url, token), daemon=True)
        self._thread.start()

    def _run(self, url: str, token: str) -> None:
        try:
            with httpx.stream(
                "GET", url, params={"t": token, "since": 10**6}, timeout=httpx.Timeout(20, read=20)
            ) as response:
                self.status = response.status_code
                for line in response.iter_lines():
                    self.lines.append(line)
        except httpx.HTTPError:
            pass
        finally:
            self.ended.set()

    def connected(self) -> bool:
        for _ in range(100):
            if self.status is not None and any("presence" in line for line in self.lines):
                return True
            real_time.sleep(0.05)
        return False

    def saw(self, text: str) -> bool:
        return any(text in line for line in self.lines)


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Any]]:
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    for key in ("CHIMERA_SHARING", "CHIMERA_SHARE_EXPIRY_HOURS", "CHIMERA_SERVER_TOKEN"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    app = build_api_app(lambda: ChatSession(_Agent()), workspace=ws)
    base, server, thread = _serve(app)
    try:
        yield base, app
    finally:
        app.state.guest_server.stop()
        server.should_exit = True
        thread.join(timeout=5)
        get_settings.cache_clear()


def _session(client: httpx.Client) -> str:
    response = client.post("/api/code/turn", json={"message": "hello"})
    for line in response.text.splitlines():
        if line.startswith("data: ") and '"session_id"' in line:
            data = json.loads(line[len("data: ") :])
            sid = data.get("session_id")
            if sid:
                return str(sid)
    raise AssertionError("no session in the turn's stream")


def test_on_a_real_server_revoking_ends_an_open_guest_stream_on_both_doors(
    served: tuple[str, Any],
) -> None:
    base, app = served
    client = httpx.Client(base_url=base, timeout=20)
    sid = _session(client)
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    door = client.post("/api/code/share/network", json={"port": 0}).json()
    mounted = _Reader(f"{base}/guest/api/live", token)
    lan = _Reader(f"http://127.0.0.1:{door['port']}/api/live", token)
    assert mounted.connected() and lan.connected()
    assert mounted.status == 200 and lan.status == 200

    assert client.delete("/api/security/access/links").json() == {"revoked": 1}
    # Ended by the revoke itself — well inside the fifteen-second heartbeat.
    assert mounted.ended.wait(3) and lan.ended.wait(3)
    app.state.session_bus.publish(sid, "token", {"text": SECRET})
    real_time.sleep(0.3)
    assert not mounted.saw(SECRET) and not lan.saw(SECRET)


def test_on_a_real_server_sharing_off_ends_an_open_guest_stream_on_both_doors(
    served: tuple[str, Any],
) -> None:
    base, app = served
    client = httpx.Client(base_url=base, timeout=20)
    sid = _session(client)
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    door = client.post("/api/code/share/network", json={"port": 0}).json()
    mounted = _Reader(f"{base}/guest/api/live", token)
    lan = _Reader(f"http://127.0.0.1:{door['port']}/api/live", token)
    assert mounted.connected() and lan.connected()

    assert client.patch("/api/config", json={"CHIMERA_SHARING": "false"}).status_code == 200
    assert mounted.ended.wait(3) and lan.ended.wait(3)
    app.state.session_bus.publish(sid, "token", {"text": SECRET})
    real_time.sleep(0.3)
    assert not mounted.saw(SECRET) and not lan.saw(SECRET)
    # The link is kept, inert — the same fact the card states.
    assert client.get("/guest/api/session", params={"t": token}).status_code == 401
