"""JSON-RPC 2.0 over a child process, in both directions.

ACP is symmetric: we call the agent (``initialize``, ``session/new``, ``session/prompt``) and the
agent calls us (``fs/read_text_file``, ``session/request_permission``) while our own call is still
outstanding. That is the whole reason this is not a request/response client — a loop that waited for
its own reply before reading the next frame would deadlock on the first permission prompt, and it
would deadlock in the shape everybody recognises: "the agent stopped responding".
"""

from __future__ import annotations

import itertools
import queue
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from chimera.proc.stdio import StdioProcess
from chimera.telemetry import get_logger

_log = get_logger("acp.client")

#: JSON-RPC's reserved codes, for the two answers we ever give.
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


class AcpError(RuntimeError):
    """The agent answered with an error, or stopped being able to answer at all."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class _Pending:
    """One outstanding call, waiting on the reader thread to hand it an answer."""

    __slots__ = ("done", "error", "result")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Any = None
        self.error: dict[str, Any] | None = None


class AcpConnection:
    """A live ACP peer: a child process, a request table, and a place to put its questions."""

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
        on_notify: Callable[[str, dict[str, Any]], None] | None = None,
        on_exit: Callable[[int], None] | None = None,
        name: str = "acp",
    ) -> None:
        self.name = name
        #: Methods the agent may call on US. A method not in here is answered with "method not
        #: found", which is the protocol's way of saying "we did not offer that capability" — and
        #: it is an answer, not silence, because silence is what makes an agent hang.
        self.handlers = dict(handlers or {})
        self._on_notify = on_notify
        self._ids = itertools.count(1)
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._closed = threading.Event()
        self._exit_code: int | None = None
        #: Incoming requests run on their own thread, in arrival order. Handling them on the reader
        #: thread would mean a permission prompt that waits for a human also stops us reading — so
        #: the agent's next frame, including the one saying it gave up, would never be seen.
        self._inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._proc = StdioProcess(
            argv,
            cwd=cwd,
            env=env,
            on_message=self._dispatch,
            on_exit=self._died,
            name=name,
        )
        self._user_on_exit = on_exit

    # --- lifetime ---------------------------------------------------------------------------

    def start(self) -> AcpConnection:
        self._proc.start()
        threading.Thread(target=self._serve_inbox, name=f"{self.name}-rpc", daemon=True).start()
        return self

    def close(self, timeout: float = 5.0) -> None:
        self._closed.set()
        self._inbox.put(None)
        self._proc.close(timeout=timeout)
        # Anything still waiting will never be answered. Failing them loudly beats leaving a caller
        # blocked on an event that nothing will ever set.
        self._fail_pending("the agent connection closed")

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def diagnostics(self) -> str:
        """The child's last words, for a failure message that can actually be acted on."""
        return self._proc.stderr_tail()

    # --- outgoing ---------------------------------------------------------------------------

    def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 120.0) -> Any:
        """Call the agent and wait. Raises :class:`AcpError` on an error reply, a dead child, or a
        timeout — three different sentences, because they need three different fixes."""
        if self._closed.is_set():
            raise AcpError(f"{self.name} is not running")
        ident = next(self._ids)
        pending = _Pending()
        with self._pending_lock:
            self._pending[ident] = pending
        try:
            self._proc.send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})
        except BrokenPipeError as exc:
            with self._pending_lock:
                self._pending.pop(ident, None)
            raise AcpError(f"{self.name} stopped before {method} could be sent") from exc

        if not pending.done.wait(timeout):
            with self._pending_lock:
                self._pending.pop(ident, None)
            raise AcpError(f"{self.name} did not answer {method} within {timeout:g}s")
        if pending.error is not None:
            raise AcpError(
                str(pending.error.get("message") or f"{method} failed"),
                code=pending.error.get("code"),
                data=pending.error.get("data"),
            )
        return pending.result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Fire and forget. Used for cancellation, which must not wait for the thing it cancels."""
        # Cancelling an agent that already exited is the outcome we wanted.
        with suppress(BrokenPipeError):
            self._proc.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # --- incoming ---------------------------------------------------------------------------

    def _dispatch(self, message: dict[str, Any]) -> None:
        """Sort one frame. Runs on the reader thread and must never block."""
        if "method" in message:
            if "id" in message:
                self._inbox.put(message)
            elif self._on_notify is not None:
                # Notifications stay ON the reader thread, so `session/update` frames reach the
                # screen in the order the agent produced them. Moving them to a worker would let a
                # tool's "completed" overtake its "started".
                params = message.get("params")
                self._on_notify(str(message["method"]), params if isinstance(params, dict) else {})
            return

        ident = message.get("id")
        if not isinstance(ident, int):
            return  # a reply to a call we never made, or a frame we do not understand
        with self._pending_lock:
            pending = self._pending.pop(ident, None)
        if pending is None:
            return
        error = message.get("error")
        if isinstance(error, dict):
            pending.error = error
        else:
            pending.result = message.get("result")
        pending.done.set()

    def _serve_inbox(self) -> None:
        while True:
            message = self._inbox.get()
            if message is None:
                return
            self._answer(message)

    def _answer(self, message: dict[str, Any]) -> None:
        ident = message.get("id")
        method = str(message.get("method"))
        params = message.get("params")
        handler = self.handlers.get(method)
        if handler is None:
            self._reply_error(ident, METHOD_NOT_FOUND, f"{method} is not available from this client")
            return
        try:
            result = handler(params if isinstance(params, dict) else {})
        except Exception as exc:  # noqa: BLE001 — becomes the agent's error reply, not our crash
            _log.warning("%s: handler for %s failed: %s", self.name, method, exc)
            # The message goes to another program's log, so it carries the reason and not a
            # traceback: an agent that is told "refused: outside the write region" can adapt, and
            # one that is told "internal error" can only retry.
            self._reply_error(ident, INTERNAL_ERROR, str(exc) or "the client could not do that")
            return
        self._reply(ident, result)

    def _reply(self, ident: Any, result: Any) -> None:
        with_result = {"jsonrpc": "2.0", "id": ident, "result": result if result is not None else {}}
        with suppress(BrokenPipeError):
            self._proc.send(with_result)

    def _reply_error(self, ident: Any, code: int, message: str) -> None:
        with suppress(BrokenPipeError):
            self._proc.send({"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}})

    # --- the child going away -----------------------------------------------------------------

    def _died(self, code: int) -> None:
        self._exit_code = code
        detail = self.diagnostics()
        self._fail_pending(
            f"{self.name} exited with code {code}" + (f":\n{detail}" if detail else "")
        )
        self._inbox.put(None)
        if self._user_on_exit is not None:
            self._user_on_exit(code)

    def _fail_pending(self, message: str) -> None:
        with self._pending_lock:
            waiting = list(self._pending.values())
            self._pending.clear()
        for pending in waiting:
            pending.error = {"code": INTERNAL_ERROR, "message": message}
            pending.done.set()
