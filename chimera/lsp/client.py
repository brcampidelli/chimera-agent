"""Driving `ruff server` for diagnostics.

`ruff` rather than a general language server, and the reason is not convenience: it is already a
dev-dependency of this project, it is a single binary with no Node runtime behind it, and — the part
that matters — **it returns the same diagnostics CI enforces**. A squiggle in the editor that does
not correspond to something the build will complain about trains people to ignore squiggles.

The protocol subset here is deliberately small. `initialize`, `didOpen`, `didChange`, `didClose`,
and the `publishDiagnostics` notification coming back. No hover, no completion, no go-to-definition
— those are worth having and none of them is worth having half of.

**Whole-document sync, not incremental.** LSP offers both. Incremental sends only the edited range
and requires the client and server to agree, forever, on the exact state of every open buffer; a
single dropped or misordered change desynchronises them and every subsequent diagnostic points at
text the server thinks is there. Whole-document sends the file on each change, which for source
files is a few kilobytes against a process on the same machine. The plan called for incremental;
this is the cheaper thing that cannot silently lie, and the honest note is that it costs bandwidth
we are not short of.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.lsp.transport import LspTransport
from chimera.telemetry import get_logger

_log = get_logger("lsp.client")

#: LSP severity numbers, in the protocol's own order.
SEVERITY = {1: "error", 2: "warning", 3: "information", 4: "hint"}


@dataclass(frozen=True)
class Diagnostic:
    """One problem, in the editor's coordinates."""

    path: str
    line: int  # 0-based, as the protocol sends it
    column: int  # UTF-16 code units, as the protocol sends it
    end_line: int
    end_column: int
    severity: str  # "error" | "warning" | "information" | "hint"
    code: str  # the rule that fired, e.g. "F401"
    message: str


def path_to_uri(path: Path) -> str:
    """A filesystem path as a `file://` URI.

    `Path.as_uri()` and not string formatting: a Windows path needs a drive letter, three slashes
    and percent-encoding for spaces, and every hand-rolled version of this gets one of the three
    wrong on the first path containing "Program Files".
    """
    return Path(path).resolve().as_uri()


def uri_to_path(uri: str) -> str:
    """The inverse, best-effort. Returns the uri unchanged when it is not a file URI."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    path = unquote(parsed.path)
    # Windows: `/C:/x` → `C:/x`.
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


class RuffClient:
    """A running `ruff server`, and the documents it has been told about."""

    def __init__(
        self,
        workspace: Path,
        *,
        command: list[str] | None = None,
        on_diagnostics: Callable[[str, list[Diagnostic]], None] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.command = command or ["ruff", "server"]
        self._on_diagnostics = on_diagnostics
        self._transport: LspTransport | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, Any] = {}
        self._lock = threading.Lock()
        #: The latest diagnostics per file URI, so a caller that arrives after the notification can
        #: still be told. A push-only design means whoever asks a moment too late gets nothing and
        #: concludes the file is clean.
        self.diagnostics: dict[str, list[Diagnostic]] = {}
        #: Version per open document, incremented on every change. The protocol requires it to be
        #: monotonic; a server that sees a version go backwards is entitled to ignore the edit.
        self._versions: dict[str, int] = {}
        #: The last text we sent per URI, so an unchanged buffer costs nothing.
        self._texts: dict[str, str] = {}
        #: How many times the server has published for a URI. A COUNT and not a flag, because the
        #: question a caller has is never "are there diagnostics" but "have they been recomputed
        #: since I handed over this text" — and for a file that is now clean the answer is an empty
        #: list, which a flag on emptiness could not distinguish from silence.
        self._publishes: dict[str, int] = {}
        self._published = threading.Condition()

    # --- lifetime ---------------------------------------------------------------------------

    def start(self, *, timeout: float = 30.0) -> RuffClient:
        self._transport = LspTransport(
            self.command,
            cwd=self.workspace,
            on_message=self._dispatch,
            name="ruff",
        )
        self._transport.start()
        self._request(
            "initialize",
            {
                "processId": None,
                "rootUri": path_to_uri(self.workspace),
                "capabilities": {
                    "textDocument": {
                        "publishDiagnostics": {"relatedInformation": False},
                        "synchronization": {"dynamicRegistration": False},
                    }
                },
                "clientInfo": {"name": "chimera"},
            },
            timeout=timeout,
        )
        self._notify("initialized", {})
        return self

    def close(self) -> None:
        if self._transport is None:
            return
        # `shutdown` then `exit` is the protocol's own sequence, and skipping it leaves ruff writing
        # into a closed pipe — noise in the log for a shutdown that was ours.
        with _quiet():
            self._request("shutdown", None, timeout=5.0)
            self._notify("exit", None)
        self._transport.close()
        self._transport = None

    def is_alive(self) -> bool:
        return self._transport is not None and self._transport.poll() is None

    def diagnostics_for(self, path: Path) -> list[Diagnostic]:
        """The latest diagnostics for a file, or [] when none have arrived."""
        with self._published:
            return list(self.diagnostics.get(path_to_uri(path), []))

    # --- documents --------------------------------------------------------------------------

    def sync_document(self, path: Path, text: str) -> bool:
        """Hand the server this buffer. Returns whether anything was actually sent.

        A second `didOpen` for a document the server already has is outside the protocol, and it
        resets the version to 1 — a server that sees a version go backwards is entitled to ignore
        the edit. `ruff` in fact tolerates it and re-publishes anyway (measured, by reverting this
        line and watching the tests still pass), but that is undefined behaviour we would be
        depending on, and the next server we add is not obliged to be as forgiving.

        The bug this pairs with was elsewhere and is worth naming, because I got its cause wrong at
        first: the caller used to poll its own diagnostics cache and stop as soon as it was
        non-empty, so a second request returned the FIRST request's answer before the server had
        said anything about the new text. Delete the unused import and the squiggle stayed —
        precisely the failure this feature exists to prevent. `wait_for_publish` is the fix; this
        method is protocol hygiene alongside it.
        """
        uri = path_to_uri(path)
        if self._texts.get(uri) == text:
            return False
        self._texts[uri] = text
        if uri in self._versions:
            self.change_document(path, text)
        else:
            self.open_document(path, text)
        return True

    def publish_count(self, path: Path) -> int:
        """How many results the server has published for this file so far."""
        with self._published:
            return self._publishes.get(path_to_uri(path), 0)

    def wait_for_publish(self, path: Path, since: int, timeout: float) -> list[Diagnostic]:
        """Block until the server publishes again for this file, then report.

        `since` must be read BEFORE the text is sent. Reading it after leaves a window in which the
        answer arrives, the count moves, and the wait then blocks for a result that has already
        been delivered — a two-second stall on every keystroke of a fast server.
        """
        uri = path_to_uri(path)
        deadline = time.monotonic() + timeout
        with self._published:
            while self._publishes.get(uri, 0) <= since:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._published.wait(remaining)
            return list(self.diagnostics.get(uri, []))

    def open_document(self, path: Path, text: str) -> None:
        uri = path_to_uri(path)
        self._versions[uri] = 1
        self._texts[uri] = text
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": 1,
                    "text": text,
                }
            },
        )

    def change_document(self, path: Path, text: str) -> None:
        """Tell the server the whole new text. See the module docstring for why not a range."""
        uri = path_to_uri(path)
        version = self._versions.get(uri, 0) + 1
        self._versions[uri] = version
        self._texts[uri] = text
        self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}],
            },
        )

    def close_document(self, path: Path) -> None:
        uri = path_to_uri(path)
        self._versions.pop(uri, None)
        self._texts.pop(uri, None)
        with self._published:
            self.diagnostics.pop(uri, None)
            self._publishes.pop(uri, None)
        self._notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    # --- protocol ---------------------------------------------------------------------------

    def _request(self, method: str, params: Any, *, timeout: float = 30.0) -> Any:
        transport = self._transport
        if transport is None:
            raise RuntimeError("the language server is not running")
        ident = next(self._ids)
        done = threading.Event()
        with self._lock:
            self._pending[ident] = done
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": ident, "method": method}
        if params is not None:
            message["params"] = params
        transport.send(message)
        if not done.wait(timeout):
            with self._lock:
                self._pending.pop(ident, None)
            raise TimeoutError(f"ruff did not answer {method} within {timeout:g}s")
        with self._lock:
            return self._results.pop(ident, None)

    def _notify(self, method: str, params: Any) -> None:
        if self._transport is None:
            return
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        with _quiet():
            self._transport.send(message)

    def _dispatch(self, message: dict[str, Any]) -> None:
        ident = message.get("id")
        if isinstance(ident, int) and "method" not in message:
            with self._lock:
                self._results[ident] = message.get("result")
                waiter = self._pending.pop(ident, None)
            if waiter is not None:
                waiter.set()
            return

        if message.get("method") == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = str(params.get("uri") or "")
            found = [_diagnostic(uri, d) for d in (params.get("diagnostics") or [])]
            with self._published:
                self.diagnostics[uri] = found
                self._publishes[uri] = self._publishes.get(uri, 0) + 1
                self._published.notify_all()
            if self._on_diagnostics is not None:
                self._on_diagnostics(uri, found)
            return

        if isinstance(ident, int) and "method" in message:
            # A request FROM the server. We advertised nothing that needs answering, but a request
            # left unanswered is a server waiting forever, so it gets an explicit refusal.
            with _quiet():
                assert self._transport is not None
                self._transport.send(
                    {
                        "jsonrpc": "2.0",
                        "id": ident,
                        "error": {"code": -32601, "message": "not supported by this client"},
                    }
                )


def _diagnostic(uri: str, raw: dict[str, Any]) -> Diagnostic:
    span = raw.get("range") or {}
    start = span.get("start") or {}
    end = span.get("end") or {}
    return Diagnostic(
        path=uri_to_path(uri),
        line=int(start.get("line") or 0),
        column=int(start.get("character") or 0),
        end_line=int(end.get("line") or 0),
        end_column=int(end.get("character") or 0),
        severity=SEVERITY.get(int(raw.get("severity") or 1), "error"),
        code=str(raw.get("code") or ""),
        message=str(raw.get("message") or ""),
    )


class _quiet:
    """Swallow a broken pipe. The server going away mid-shutdown is the outcome we asked for."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, *_rest: Any) -> bool:
        return bool(exc_type is not None and issubclass(exc_type, (BrokenPipeError, TimeoutError)))
