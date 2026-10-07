"""`chimera sessions list|attach|logs|stop` — the app's running coding turns, from a terminal.

S30-66. The turns keep running in the app after the screen that started them is gone; the terminal
reaches them through the same bridge door `chimera code` uses, over the app's own registry of running
turns and each turn's recorded frames — no second store. `attach` follows a turn and steers it.

And the word kept its meaning: `chimera sessions` alone still lists the saved terminal threads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from chimera.cli import code_cmd, sessions_cmd
from chimera.cli.code_cmd import AppCode

TOKEN = "tok-" + "Q" * 40
URL = "http://127.0.0.1:65021"
TURN = {
    "turn_id": "ab12cd34ef",
    "session_id": "s-1",
    "workspace": "/work/login",
    "message": "refactor the login",
    "started_at": 0,
    "live_since": 0,
    "transcript_saved": False,
}
FRAMES = [
    {"event": "token", "text": "Reading the module", "seq": 1},
    {"event": "tool", "name": "read_file", "ok": True, "seq": 2},
    {"event": "guidance", "text": "keep the old API", "author": "owner", "seq": 3},
    {"event": "token", "text": " and keeping the old API.", "seq": 4},
    {"event": "done", "stopped_reason": "final", "model": "m/x", "steps": 3, "seq": 5},
]


class FakeBridge:
    def __init__(self, *, running: list[dict[str, Any]] | None = None,
                 batches: list[list[dict[str, Any]]] | None = None, guidance_status: int = 200) -> None:
        self.running = [TURN] if running is None else running
        self.batches = list(batches if batches is not None else [FRAMES])
        self.guidance_status = guidance_status
        self.calls: list[dict[str, Any]] = []

    def routes(self) -> list[str]:
        return [c["route"] for c in self.calls]

    def __call__(self, method: str, url: str, token: str, body: Any, timeout: float
                 ) -> tuple[int | None, Any]:
        assert token == TOKEN and url.endswith("/api/bridge/call")
        self.calls.append(body)
        route = body["route"]

        def ok(data: Any, status: int = 200) -> tuple[int, Any]:
            return 200, {"route": route, "status": status, "data": data}

        if route == "conversations.running":
            return ok(self.running)
        if route == "conversations.turn":
            frames = self.batches.pop(0) if self.batches else []
            seq = max((f["seq"] for f in frames), default=body["params"].get("since", 0))
            return ok({"turn_id": body["params"]["turn_id"], "frames": frames, "seq": seq})
        if route == "conversations.guidance":
            if self.guidance_status != 200:
                return ok({"detail": "that turn is not running any more; nothing was queued"},
                          self.guidance_status)
            return ok({"turn_id": body["params"]["turn_id"], "queued": True})
        if route == "conversations.stop":
            return ok({"turn_id": body["params"]["turn_id"], "stopping": True})
        return 404, {"detail": "not scripted"}


def _run(monkeypatch: pytest.MonkeyPatch, bridge: FakeBridge, args: list[str], stdin: str = "") -> Any:
    from chimera.cli.main import app

    found = {"url": URL, "token": TOKEN, "pid": 1, "version": "x", "full": False}
    monkeypatch.setattr(
        code_cmd, "_client", lambda: AppCode(discover=lambda: cast(Any, found), http=bridge)
    )
    monkeypatch.setattr(sessions_cmd, "POLL_SECONDS", 0.0)
    monkeypatch.setenv("COLUMNS", "200")
    return CliRunner().invoke(app, ["sessions", *args], input=stdin)


def test_list_shows_the_running_turns_with_the_id_the_others_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = FakeBridge()
    result = _run(monkeypatch, bridge, ["list"])
    assert result.exit_code == 0, result.output
    assert TURN["turn_id"] in result.output and "refactor the login" in result.output
    assert bridge.routes() == ["conversations.running"]


def test_list_says_when_nothing_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, FakeBridge(running=[]), ["list"])
    assert result.exit_code == 0
    assert "nothing is running" in result.output


def test_logs_reads_a_finished_turn_from_its_record(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge(running=[])
    result = _run(monkeypatch, bridge, ["logs", "ab12cd34ef"])
    assert result.exit_code == 0, result.output
    assert "Reading the module" in result.output and "and keeping the old API." in result.output
    assert "read_file ok" in result.output
    assert "read by the agent: keep the old API" in result.output
    turn_calls = [c for c in bridge.calls if c["route"] == "conversations.turn"]
    assert turn_calls[0]["params"] == {"turn_id": "ab12cd34ef", "since": 0}


def test_attach_steers_first_then_follows_to_the_end_by_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = FakeBridge(batches=[FRAMES[:2], [], FRAMES[2:]])
    result = _run(monkeypatch, bridge, ["attach", "ab12", "--say", "keep the old API", "--no-steer"])

    assert result.exit_code == 0, result.output
    sent = [c for c in bridge.calls if c["route"] == "conversations.guidance"]
    assert sent == [{"route": "conversations.guidance", "params": {"turn_id": "ab12cd34ef"},
                     "body": {"text": "keep the old API"}, "wait_seconds": 0.0}]
    assert "queued" in result.output
    assert "read by the agent: keep the old API" in result.output
    assert "m/x" in result.output and "3 steps" in result.output
    # Each poll asks only for what it has not printed.
    sinces = [c["params"]["since"] for c in bridge.calls if c["route"] == "conversations.turn"]
    assert sinces == [0, 2, 2]


def test_attach_types_lines_as_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    sent = threading.Event()

    class Steered(FakeBridge):
        def __call__(self, method: str, url: str, token: str, body: Any, timeout: float
                     ) -> tuple[int | None, Any]:
            if body["route"] == "conversations.guidance":
                sent.set()
            if body["route"] == "conversations.turn" and not sent.is_set():
                # Still working, nothing new, until the typed line has gone out.
                self.calls.append(body)
                return 200, {"route": "conversations.turn", "status": 200,
                             "data": {"turn_id": TURN["turn_id"], "frames": [], "seq": 0}}
            return super().__call__(method, url, token, body, timeout)

    bridge = Steered()
    result = _run(monkeypatch, bridge, ["attach", "ab12cd34ef", "--steer"], stdin="use the v2 client\n")

    assert result.exit_code == 0, result.output
    bodies = [c["body"] for c in bridge.calls if c["route"] == "conversations.guidance"]
    assert bodies == [{"text": "use the v2 client"}]


def test_attach_to_a_turn_that_is_not_running_says_where_to_look(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(monkeypatch, FakeBridge(running=[]), ["attach", "zz"])
    assert result.exit_code == 1
    assert "no running turn zz" in result.output
    assert "sessions logs" in result.output


def test_a_refused_guidance_is_said_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge(guidance_status=409)
    result = _run(monkeypatch, bridge, ["attach", "ab12cd34ef", "-m", "late", "--no-steer"])
    assert result.exit_code == 0, result.output
    assert "not sent" in result.output and "not running any more" in result.output


def test_attach_ends_when_the_turn_vanishes_without_a_last_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Vanishing(FakeBridge):
        polls = 0

        def __call__(self, method: str, url: str, token: str, body: Any, timeout: float
                     ) -> tuple[int | None, Any]:
            if body["route"] == "conversations.running":
                type(self).polls += 1
                self.running = [TURN] if type(self).polls == 1 else []
            return super().__call__(method, url, token, body, timeout)

    result = _run(monkeypatch, Vanishing(batches=[]), ["attach", "ab12cd34ef", "--no-steer"])
    assert result.exit_code == 0, result.output
    assert "no longer running" in result.output


def test_stop_reaches_the_turn_by_its_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge()
    result = _run(monkeypatch, bridge, ["stop", "ab12"])
    assert result.exit_code == 0, result.output
    stop = [c for c in bridge.calls if c["route"] == "conversations.stop"]
    assert stop and stop[0]["params"] == {"turn_id": "ab12cd34ef"}
    assert "step in progress finishes first" in result.output


def test_stop_of_nothing_running_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge(running=[])
    result = _run(monkeypatch, bridge, ["stop", "ab12"])
    assert result.exit_code == 1
    assert "conversations.stop" not in bridge.routes()


def test_sessions_alone_still_lists_the_saved_terminal_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()
    try:
        empty = CliRunner().invoke(app, ["sessions"])
        assert empty.exit_code == 0, empty.output
        assert "no saved conversations yet" in empty.output
        gone = CliRunner().invoke(app, ["sessions", "--delete", "nope"])
        assert gone.exit_code == 0 and "no session nope" in gone.output
    finally:
        get_settings.cache_clear()
