"""Closing the network door closes the connections through it, and the door is never called shut
while one is still open.

Found by an adversarial review of study 29, P5.5, measured on a real server: with a guest on the
LAN holding a live stream, switching sharing off took 5.0 s, and the card then said "Closed. Share
links open only from this computer." while the old listener thread was alive and still streaming
the conversation to that guest. `DELETE /api/code/share/network` did the same. uvicorn's graceful
shutdown waits for every connection to finish, a live stream never does, `thread.join(timeout=5)`
gave up inside the request, and `stop()` had already cleared the state the card reads.

What is pinned here:

* closing the door ends the streams that came through it first, so the close is quick, the guest's
  connection ends, and a frame published afterwards reaches nobody;
* a stream that came through the door checks the door before every frame, so even one nobody ended
  delivers nothing once the door is closing;
* the door is reported open for as long as its thread lives — closing, it is ``open`` and not
  ``listening``, with no address to hand out;
* a connection nothing ends is cut by the listener's grace, so the close always finishes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import chimera.api.guest_api as guest_api
from chimera.api.guest_api import GuestServer, build_guest_app
from chimera.api.sharing import SessionBus, ShareStore
from tests.test_an_open_guest_stream_closes_when_its_link_does import (
    SECRET,
    _Reader,
    _session,
    served,  # noqa: F401 -- a fixture, used by name
)


def _wait(condition: Any, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return bool(condition())


@pytest.fixture
def bare_door(tmp_path: Path) -> Iterator[tuple[GuestServer, SessionBus, str]]:
    """A guest app on its own LAN listener with NO hook that ends streams on close: what is left is
    the door's own defences — the per-frame check and the grace."""
    store = ShareStore(tmp_path / "code_shares.json")
    bus = SessionBus()
    share = store.mint("s1")

    async def no_turn(*_a: Any, **_k: Any) -> None:
        return None

    guest = build_guest_app(
        store=store,
        bus=bus,
        session_view=lambda _sid: {"exchanges": []},
        session_workspace=lambda _sid: "",
        start_turn=no_turn,
    )
    door = GuestServer(guest)
    door.start(0, host="127.0.0.1")
    try:
        yield door, bus, share.token
    finally:
        door.stop()


def test_closing_the_door_ends_the_streams_through_it_at_once(
    served: tuple[str, Any],  # noqa: F811 -- the fixture imported above
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A grace far longer than the test: if the close is quick it is because the streams were ended,
    # not because uvicorn gave up on them.
    monkeypatch.setattr(guest_api, "GRACE_SECONDS", 30)
    base, app = served
    client = httpx.Client(base_url=base, timeout=20)
    sid = _session(client)
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    door = client.post("/api/code/share/network", json={"port": 0}).json()
    lan = _Reader(f"http://127.0.0.1:{door['port']}/api/live", token)
    mounted = _Reader(f"{base}/guest/api/live", token)
    assert lan.connected() and mounted.connected()

    started = time.monotonic()
    closed = client.delete("/api/code/share/network").json()
    assert time.monotonic() - started < 2.5
    assert closed == {"open": False, "port": None, "urls": []}
    assert lan.ended.wait(3)
    app.state.session_bus.publish(sid, "token", {"text": SECRET})
    time.sleep(0.3)
    assert not lan.saw(SECRET)
    # The door is the LAN's: the owner's own /guest mount, with a link that still opens, goes on.
    assert not mounted.ended.is_set() and mounted.saw(SECRET)
    assert client.get("/api/security/access").json()["guest_door"]["open"] is False


def test_a_closing_door_is_reported_open_and_sends_nothing_more_through_it(
    bare_door: tuple[GuestServer, SessionBus, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    door, bus, token = bare_door
    # Nothing ends the stream (no hook) and the grace is long, so the thread outlives the close.
    monkeypatch.setattr(guest_api, "STOP_WAIT_SECONDS", 0.0)
    door._server.config.timeout_graceful_shutdown = 30  # noqa: SLF001 -- the running listener's grace
    port = door.port
    lan = _Reader(f"http://127.0.0.1:{port}/api/live", token)
    assert lan.connected()

    door.stop()
    # Closed to new guests, still carrying one: open, not listening, nothing to hand out.
    assert door.open is True and door.listening is False
    assert door.port == port and door.urls() == [] and door.urls(token) == []
    with pytest.raises(OSError):
        door.start(0, host="127.0.0.1")  # no second listener beside one still closing

    bus.publish("s1", "token", {"text": SECRET})
    assert lan.ended.wait(3)  # the frame met the door's check, and the stream ended there
    assert not lan.saw(SECRET)
    assert _wait(lambda: not door.open, 5)
    assert door.port is None


#: Long enough for a starved runner, short of "forever": without the grace, uvicorn waits for a live
#: stream that never ends, so the thread would still be alive here. Measured on 2026-10-16, idle and
#: with 48 busy processes beside it, the thread dies 1.14-1.25 s after `stop()`.
CLOSE_FINISHES_WITHIN = 10.0


def test_a_connection_nothing_ends_is_cut_by_the_grace_and_the_close_finishes(
    bare_door: tuple[GuestServer, SessionBus, str],
) -> None:
    # What the door promises is that the close FINISHES — the grace cuts a stream nothing ends — and
    # that until it has, the door is never reported shut. It does not promise to finish inside
    # `stop()`'s own wait: past STOP_WAIT_SECONDS `stop()` returns with the door still `open`, by
    # design. This test used to assert `elapsed < STOP_WAIT_SECONDS`, which a Windows CI runner
    # starved for two seconds failed at 3.015 s (PR #952) with nothing wrong in the door.
    door, _bus, token = bare_door
    lan = _Reader(f"http://127.0.0.1:{door.port}/api/live", token)
    assert lan.connected()
    thread = door._thread  # noqa: SLF001 -- the listener whose end is the claim
    assert thread is not None

    closer = threading.Thread(target=door.stop)
    closer.start()
    closer.join(timeout=CLOSE_FINISHES_WITHIN)
    assert not closer.is_alive(), "stop() never returned"
    # Whenever stop() returns, the door tells the truth about the listener behind it.
    assert door.open is thread.is_alive()
    thread.join(timeout=CLOSE_FINISHES_WITHIN)
    assert not thread.is_alive(), "the grace did not cut the connection: the close never finished"
    assert door.open is False and door.port is None and door.urls() == []
    assert lan.ended.wait(3)
