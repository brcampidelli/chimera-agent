"""Chimera answering an editor — the mirror of the client half, driven the way an editor drives it.

These speak the wire rather than calling the methods: frames in, frames out, parsed back. A test that
called `_session_prompt` directly would pass with the dispatcher broken, and the dispatcher is the
part an editor actually touches.

The rule everything bends around is that **stdout is the protocol**. A stray print corrupts the frame
the editor is mid-way through parsing, and the symptom is an editor that hangs rather than output in
the wrong place — so one test asserts nothing but well-formed JSON comes out, and it is the most
valuable one here.
"""

from __future__ import annotations

import io
import json
from typing import Any

from chimera.acp.server import PROTOCOL_VERSION, AcpServer


def _drive(frames: list[dict[str, Any]], run_turn: Any) -> list[dict[str, Any]]:
    """Feed frames through a server and parse everything it wrote."""
    out = io.StringIO()
    server = AcpServer(
        run_turn,
        stdin=io.StringIO("\n".join(json.dumps(f) for f in frames) + "\n"),
        stdout=out,
        workspace="/ws",
    )
    server.serve_forever()
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def _echo(prompt: str, on_token: Any) -> str:
    on_token(f"you said: {prompt}")
    return f"you said: {prompt}"


def _silent(prompt: str, on_token: Any) -> str:
    """A backend that never streams — the case that would otherwise answer nothing at all."""
    return f"quietly: {prompt}"


# --- the handshake ------------------------------------------------------------------------------


def test_initialize_declares_only_what_is_true() -> None:
    """`loadSession: false` is the point of this test.

    Sessions here live in memory for the life of the process, so accepting a resume would fail on
    the SECOND turn — in the editor, in front of somebody with no way to see why. An honest no costs
    a feature; a dishonest yes costs trust in every other answer in the dict.
    """
    out = _drive([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}], _echo)

    caps = out[0]["result"]["agentCapabilities"]
    assert out[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert caps["loadSession"] is False
    assert caps["promptCapabilities"]["image"] is False


def test_a_session_gets_an_id_and_a_prompt_needs_a_real_one() -> None:
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {"cwd": "/repo"}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/prompt",
            "params": {"sessionId": "not-a-session", "prompt": "hi"},
        },
    ]

    out = _drive(frames, _echo)

    assert out[0]["result"]["sessionId"]
    assert "error" in out[1], "an unknown session was accepted"


# --- a turn -------------------------------------------------------------------------------------


def _one_turn(run_turn: Any, prompt: str = "fix the bug") -> list[dict[str, Any]]:
    """Open a session and prompt it on ONE server, through the dispatcher.

    Two servers would not work and the reason is a property worth keeping: a session id minted by
    one process is not valid in another. `handle()` rather than `serve_forever()` because the id is
    only knowable after the first frame has been answered — the dispatcher, which is the part an
    editor touches, is still what runs.
    """
    out = io.StringIO()
    server = AcpServer(run_turn, stdin=io.StringIO(""), stdout=out, workspace="/ws")
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}})
    session_id = json.loads(out.getvalue().splitlines()[0])["result"]["sessionId"]
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]},
        }
    )
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_a_session_from_another_process_is_not_valid_here() -> None:
    """Sessions are per-process state, not global. If an id minted by one server worked in another,
    `loadSession: false` would be a lie told by the handshake and contradicted by the behaviour."""
    first = _drive([{"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}], _echo)
    stale = first[0]["result"]["sessionId"]

    out = _drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/prompt",
                "params": {"sessionId": stale, "prompt": "hello"},
            }
        ],
        _echo,
    )

    assert "error" in out[0]


def test_a_turn_streams_then_stops() -> None:
    out = _one_turn(_echo)

    updates = [f for f in out if f.get("method") == "session/update"]
    replies = [f for f in out if f.get("id") == 2 and "result" in f]
    assert updates, "nothing streamed"
    assert updates[0]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert "fix the bug" in updates[0]["params"]["update"]["content"]["text"]
    assert replies[0]["result"]["stopReason"] == "end_turn"
    assert "text" not in json.dumps(replies[0]["result"]), "the answer was duplicated into the reply"


def test_a_backend_that_never_streams_still_answers() -> None:
    """Otherwise a non-streaming model is a silent agent: the editor shows an empty bubble and a
    successful stop reason, which reads as "it had nothing to say"."""
    out = _one_turn(_silent)

    updates = [f for f in out if f.get("method") == "session/update"]
    assert updates and "quietly:" in updates[0]["params"]["update"]["content"]["text"]


def test_a_failing_turn_answers_with_an_error_rather_than_silence() -> None:
    """Silence is what makes an editor hang. An error frame is a thing it can render."""

    def boom(prompt: str, on_token: Any) -> str:
        raise RuntimeError("the loop fell over")

    out = _one_turn(boom)

    failed = [f for f in out if f.get("id") == 2]
    assert failed and "error" in failed[0]
    assert "the loop fell over" in failed[0]["error"]["message"]


# --- the rules that keep an editor working ------------------------------------------------------


def test_an_unknown_method_is_answered_not_ignored() -> None:
    # A request with an id and no reply is an editor waiting forever.
    out = _drive([{"jsonrpc": "2.0", "id": 9, "method": "session/setMode", "params": {}}], _echo)

    assert out[0]["error"]["code"] == -32601


def test_an_unknown_notification_is_ignored_not_answered() -> None:
    # The mirror: replying to something with no id gives the other side a frame it cannot match.
    out = _drive([{"jsonrpc": "2.0", "method": "some/notification", "params": {}}], _echo)

    assert out == []


def test_a_broken_frame_does_not_kill_the_connection() -> None:
    """One bad line from an editor mid-upgrade must not end the session. Everything after it still
    has to work, or a transient becomes a restart."""
    stream = io.StringIO(
        "{not json at all}\n"
        + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
    )
    out = io.StringIO()

    AcpServer(_echo, stdin=stream, stdout=out).serve_forever()

    assert json.loads(out.getvalue().strip())["result"]["protocolVersion"] == PROTOCOL_VERSION


def test_everything_written_to_stdout_is_a_frame() -> None:
    """The rule the whole module bends around.

    A stray print, a library logging to stdout, a traceback on the wrong stream — each corrupts the
    frame the editor is parsing, and the failure looks like a hang rather than like output in the
    wrong place. So: every line out, parsed, no exceptions.
    """
    out = io.StringIO()
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "nope", "params": {}},
        {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "x"}},
    ]
    AcpServer(
        _echo,
        stdin=io.StringIO("\n".join(json.dumps(f) for f in frames) + "\n"),
        stdout=out,
    ).serve_forever()

    for line in out.getvalue().splitlines():
        if line.strip():
            assert json.loads(line)["jsonrpc"] == "2.0"
