"""Chimera as somebody else's agent — the other end of `chimera/acp/client.py`.

The package docstring says this is Chimera on the CLIENT side: we are the editor, the other agent is
the worker. This module is the mirror. Speak the same protocol from the other direction and Chimera
runs *inside* Zed, JetBrains or Neovim, driven by an editor it did not write.

That is distribution rather than capability — nothing here makes the agent better at anything. What
it changes is who can reach it: the loop, the verifier, the taint ledger and the receipt become
available to a person who never installs a CLI, in the editor they already have open.

**stdout is the protocol, and that is the one rule everything else bends around.** A stray `print`,
a library that logs to stdout, a traceback escaping to the wrong stream — any of them corrupts the
frame the editor is mid-way through parsing, and the failure looks like the editor hanging rather
than like output in the wrong place. So the only thing that ever writes to stdout is `_send`, logging
goes to stderr, and the agent loop's own token stream is routed into notifications instead of
printed.

**Capabilities are declared as what is true, not as what would be impressive.** We say we can stream
tokens and report tool calls, because we do. We do not claim `loadSession` — resuming a session we
never persisted would fail on the second turn, in the editor, in front of a person who cannot see
why.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from collections.abc import Callable
from typing import Any, TextIO

from chimera.telemetry import get_logger

_log = get_logger("acp.server")

#: The protocol revision this speaks. Kept beside the client's so a mismatch is one grep, not two.
PROTOCOL_VERSION = 1

#: JSON-RPC's own codes, for the two conditions worth distinguishing to a caller.
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


class AcpServer:
    """Serve one editor over stdio. Blocking: :meth:`serve_forever` returns when stdin closes.

    ``run_turn`` is the seam. It receives the prompt text and a callback for streaming tokens, and
    returns the final answer — so this module knows about the protocol and nothing about the agent,
    and the agent knows nothing about the protocol. The alternative (an `Agent` constructed in here)
    would make every future change to the loop a change to the transport.
    """

    def __init__(
        self,
        run_turn: Callable[[str, Callable[[str], None]], str],
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        workspace: str = "",
    ) -> None:
        self.run_turn = run_turn
        self._in = stdin or sys.stdin
        self._out = stdout or sys.stdout
        self.workspace = workspace
        self.sessions: dict[str, str] = {}
        #: One lock around writes. Token notifications are emitted from the turn while the reader
        #: thread may be answering something else; two writers interleaving mid-frame produces a
        #: line that is valid JSON to neither party.
        self._write_lock = threading.Lock()
        self._cancelled: set[str] = set()
        #: Whether the current turn emitted anything through `on_token`. Tracked so a backend that
        #: does not stream still delivers its answer, without double-sending for one that does.
        self._streamed = False

    # --- the wire -----------------------------------------------------------------------------

    def _send(self, message: dict[str, Any]) -> None:
        """The only function in this process that may write to stdout."""
        with self._write_lock:
            self._out.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._out.flush()

    def _reply(self, ident: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": ident, "result": result})

    def _error(self, ident: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}})

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # --- methods ------------------------------------------------------------------------------

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Declare what is true.

        `loadSession` is absent on purpose: sessions here live in memory for the life of the
        process, so accepting a resume would fail on the second turn — in the editor, in front of
        somebody with no way to see why. An honest "no" costs a feature; a dishonest "yes" costs
        trust in every other answer in this dict.
        """
        client = params.get("clientInfo") or {}
        _log.info("acp client: %s", client.get("name") or "(unnamed)")
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True},
            },
            "agentInfo": {"name": "chimera", "title": "Chimera", "version": _version()},
        }

    def _session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        self.sessions[session_id] = str(params.get("cwd") or self.workspace)
        return {"sessionId": session_id}

    def _session_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("sessionId") or "")
        if session_id not in self.sessions:
            raise KeyError(f"unknown session {session_id!r}")
        self._cancelled.discard(session_id)
        self._streamed = False
        text = _text_of(params.get("prompt"))

        def on_token(chunk: str) -> None:
            if not chunk:
                return
            self._streamed = True
            self.notify(
                "session/update",
                {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": chunk},
                    },
                },
            )

        answer = self.run_turn(text, on_token)
        # The answer is not returned here, and that is the protocol rather than an omission: content
        # travels as `session/update` notifications, and `session/prompt` replies with why the turn
        # ended. An agent that also put the text in the reply would have editors showing it twice —
        # once streamed, once at the end.
        #
        # A loop that produced an answer without ever calling `on_token` would be invisible, so it
        # is emitted as one final chunk instead of being lost. That is not the common path; it is
        # what makes a non-streaming backend usable here at all.
        if answer and not self._streamed:
            on_token(answer)
        stop = "cancelled" if session_id in self._cancelled else "end_turn"
        return {"stopReason": stop}

    def _session_cancel(self, params: dict[str, Any]) -> None:
        """Mark the session cancelled. Cooperative, and the docstring says so rather than the name
        implying a kill: the running turn checks this between steps, so a call already in flight
        finishes. Claiming otherwise would promise an interruption the loop cannot deliver."""
        self._cancelled.add(str(params.get("sessionId") or ""))

    def cancelled(self, session_id: str) -> bool:
        return session_id in self._cancelled

    # --- the loop -----------------------------------------------------------------------------

    def handle(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        ident = message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        params = params or {}

        if method == "session/cancel":
            self._session_cancel(params)
            return
        if ident is None:
            # A notification we do not handle. Silence is correct here — a reply to something with
            # no id is a frame the other side cannot match to anything.
            return

        try:
            if method == "initialize":
                self._reply(ident, self._initialize(params))
            elif method == "session/new":
                self._reply(ident, self._session_new(params))
            elif method == "session/prompt":
                self._reply(ident, self._session_prompt(params))
            else:
                # An answer, not silence. Silence is what makes an editor hang forever on a method
                # we never implemented — the failure the client half of this package documents.
                self._error(ident, _METHOD_NOT_FOUND, f"method not found: {method}")
        except Exception as exc:  # noqa: BLE001 — a failed turn must not take the connection down
            _log.warning("acp method %s failed: %s", method, exc)
            self._error(ident, _INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    def serve_forever(self) -> None:
        """Read frames until stdin closes. One line per message, same framing as the client."""
        for line in self._in:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                _log.warning("acp: dropping unparseable frame")
                continue
            if isinstance(message, dict):
                self.handle(message)


def _text_of(content: Any) -> str:
    """Flatten ACP content blocks to text. Unknown block types are skipped, not guessed at."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, list):
        return "".join(_text_of(item) for item in content)
    return ""


def _version() -> str:
    from chimera import __version__

    return __version__
