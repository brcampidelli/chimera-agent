"""A language server, framed the way the protocol says.

LSP does not send one JSON object per line. It sends ``Content-Length: N\\r\\n\\r\\n`` followed by
exactly N **bytes** — and that word is why this cannot reuse the ACP pump. A text-mode stream reads
CHARACTERS, so `read(n)` after a header returns n characters; for any message containing a non-ASCII
identifier, an accented docstring or a CJK string literal, that is fewer bytes than the header
promised and every subsequent frame is misaligned. The symptom is a language server that works
perfectly until someone writes a comment in Portuguese.

So the stream is binary here, and everything else — resolving the program, the Windows spawn flags,
killing the tree, the exit-time reaper — is shared with :mod:`chimera.proc.stdio` rather than
copied. One reaper, two framings.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from chimera.proc.stdio import _spawn_flags, kill_tree, resolve_program, track, untrack
from chimera.telemetry import get_logger

_log = get_logger("lsp.transport")

#: Largest frame accepted. A server announcing more than this is broken or hostile, and allocating
#: what it asked for to find out is the bug rather than the defence.
MAX_FRAME_BYTES = 32 * 1024 * 1024

#: Header bytes read before giving up on finding the blank line that ends them.
MAX_HEADER_BYTES = 8192


class LspTransport:
    """A child process exchanging Content-Length-framed JSON."""

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        on_message: Callable[[dict[str, Any]], None],
        on_exit: Callable[[int], None] | None = None,
        name: str = "lsp",
    ) -> None:
        if not argv:
            raise ValueError("argv must name a program")
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.name = name
        self._on_message = on_message
        self._on_exit = on_exit
        self._proc: subprocess.Popen[bytes] | None = None
        self._write_lock = threading.Lock()
        self._closed = threading.Event()
        self._stderr: list[str] = []

    # --- lifetime ---------------------------------------------------------------------------

    def start(self) -> LspTransport:
        program = resolve_program(self.argv[0])
        self._proc = subprocess.Popen(  # noqa: S603 — argv list, never a shell string
            [program, *self.argv[1:]],
            cwd=str(self.cwd) if self.cwd else None,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # BINARY. See the module docstring: Content-Length counts bytes, and a text-mode stream
            # counts characters.
            **_spawn_flags(),
        )
        track(self)
        threading.Thread(target=self._read_frames, name=f"{self.name}-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"{self.name}-err", daemon=True).start()
        return self

    def close(self, timeout: float = 5.0) -> None:
        self._closed.set()
        untrack(self)
        proc = self._proc
        if proc is None:
            return
        with suppress(Exception), self._write_lock:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            with suppress(Exception):
                proc.wait(timeout=timeout)

    def poll(self) -> int | None:
        return self._proc.poll() if self._proc else None

    def stderr_tail(self, lines: int = 20) -> str:
        return "\n".join(self._stderr[-lines:]).strip()

    # --- writing ----------------------------------------------------------------------------

    def send(self, message: dict[str, Any]) -> None:
        """Write one framed message. Raises ``BrokenPipeError`` once the server is gone."""
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise BrokenPipeError(f"{self.name} is not running")
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        # The length is of the ENCODED body, which is the whole point of encoding first.
        frame = b"Content-Length: %d\r\n\r\n%s" % (len(body), body)
        with self._write_lock:
            if proc.stdin.closed:
                raise BrokenPipeError(f"{self.name} closed its input")
            proc.stdin.write(frame)
            proc.stdin.flush()

    # --- reading ----------------------------------------------------------------------------

    def _read_frames(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        stream = proc.stdout
        try:
            while True:
                length = self._read_header(stream)
                if length is None:
                    break
                body = stream.read(length)
                if body is None or len(body) < length:
                    break  # the server died mid-frame
                try:
                    message = json.loads(body.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    _log.warning("%s: unparseable frame of %d bytes", self.name, length)
                    continue
                if not isinstance(message, dict):
                    continue
                try:
                    self._on_message(message)
                except Exception as exc:  # noqa: BLE001 — a handler must not kill the reader
                    _log.warning("%s: message handler failed: %s", self.name, exc)
        except (ValueError, OSError):
            pass  # the pipe closed under us; `close()` and the exit callback say what happened
        finally:
            code = proc.wait() if proc.poll() is None else proc.returncode
            if self._on_exit is not None and not self._closed.is_set():
                with suppress(Exception):
                    self._on_exit(code if code is not None else -1)

    def _read_header(self, stream: Any) -> int | None:
        """Read headers up to the blank line and return the Content-Length, or None at EOF."""
        length: int | None = None
        consumed = 0
        while True:
            line = stream.readline()
            if not line:
                return None
            consumed += len(line)
            if consumed > MAX_HEADER_BYTES:
                _log.warning("%s: header block over %d bytes", self.name, MAX_HEADER_BYTES)
                return None
            stripped = line.strip()
            if not stripped:  # the blank line that ends the headers
                if length is None:
                    continue  # a stray blank before any header: keep looking
                return length
            name, _, value = stripped.partition(b":")
            if name.strip().lower() == b"content-length":
                try:
                    candidate = int(value.strip())
                except ValueError:
                    return None
                if candidate < 0 or candidate > MAX_FRAME_BYTES:
                    _log.warning("%s: refusing a frame of %d bytes", self.name, candidate)
                    return None
                length = candidate

    def _read_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        with suppress(ValueError, OSError):
            for raw in proc.stderr:
                self._stderr.append(raw.decode("utf-8", errors="replace").rstrip("\n")[:2000])
                del self._stderr[:-200]
