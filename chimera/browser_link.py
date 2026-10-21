"""Authenticated local link between Chimera and its Chrome Native Messaging host."""

from __future__ import annotations

import contextlib
import getpass
import json
import logging
import multiprocessing.connection
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, cast

PAIRING_KEY_BYTES = 32
REQUEST_TIMEOUT_SECONDS = 20.0
RETRY_SECONDS = 2.0
MAX_LINK_MESSAGE_BYTES = 32 * 1024 * 1024
_log = logging.getLogger(__name__)


def pairing_key_path(home: Path) -> Path:
    return home / "browser" / "pairing.key"


def load_or_create_pairing_key(home: Path, *, create: bool = False) -> bytes | None:
    """Read the installation secret; create it atomically only when explicitly requested."""
    path = pairing_key_path(home)
    created = False
    try:
        value = path.read_bytes()
    except FileNotFoundError:
        if not create:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            path.parent.chmod(0o700)
        value = secrets.token_bytes(PAIRING_KEY_BYTES)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            value = path.read_bytes()
        else:
            created = True
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
    if len(value) != PAIRING_KEY_BYTES:
        raise ValueError("Chrome pairing key is invalid")
    if os.name != "nt":
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    elif created:
        import subprocess

        try:
            result = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{getpass.getuser()}:F"],
                capture_output=True,
                check=False,
            )
        except OSError:
            _log.warning("could not restrict Chrome pairing key permissions with icacls")
        else:
            if result.returncode != 0:
                _log.warning("could not restrict Chrome pairing key permissions with icacls")
    return value


def unpair(home: Path) -> bool:
    """Remove this installation's pairing secret; an authenticated host then disconnects."""
    path = pairing_key_path(home)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def endpoint(home: Path, *, username: str | None = None) -> tuple[str, str]:
    """Return the local-only address and multiprocessing family for this platform."""
    if os.name == "nt":
        user = username or getpass.getuser()
        safe_user = re.sub(r"[^A-Za-z0-9_.-]", "_", user)
        # A per-home suffix lets isolated app instances (tests, profiles) coexist under one account.
        suffix = uuid.uuid5(uuid.NAMESPACE_URL, str(home.resolve())).hex[:12]
        return rf"\\.\pipe\chimera-browser-{safe_user}-{suffix}", "AF_PIPE"
    path = home / "browser" / "host.sock"
    return str(path), "AF_UNIX"


class _Transport:
    def __init__(self, link: BrowserLink, timeout_seconds: float) -> None:
        self._link = link
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def send(self, message: dict[str, object]) -> dict[str, object]:
        request_id = message.get("id")
        with self._lock:
            connection = self._link._connection_snapshot()
            if connection is None:
                raise RuntimeError("Chrome host is not connected")
            try:
                # Connection.send/recv use pickle, which lets an authenticated peer execute code.
                # The link carries only JSON objects; never deserialize peer-controlled pickle.
                payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
                if len(payload) > MAX_LINK_MESSAGE_BYTES:
                    raise ValueError("Chrome link message too large")
                connection.send_bytes(payload)
                deadline = time.monotonic() + self._timeout_seconds
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not connection.poll(remaining):
                        raise TimeoutError(
                            f"Chrome extension did not respond within {self._timeout_seconds:g} seconds"
                        )
                    reply = json.loads(connection.recv_bytes(maxlength=MAX_LINK_MESSAGE_BYTES).decode("utf-8"))
                    if not isinstance(reply, dict):
                        raise ValueError("Chrome host reply is not an object")
                    if reply.get("id") == request_id:
                        return cast(dict[str, object], reply)
                    # A late response for another request must not satisfy this call.
            except TimeoutError:
                self._link._drop(connection)
                raise
            except (EOFError, OSError, BrokenPipeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._link._drop(connection)
                raise RuntimeError("Chrome host sent an invalid reply or disconnected") from exc
            except ValueError as exc:
                self._link._drop(connection)
                raise RuntimeError("Chrome host sent an invalid reply") from exc


class BrowserLink:
    """A single-host authenticated listener; transports are safe to share across browser calls."""

    def __init__(self, home: Path, *, request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS) -> None:
        self.home = Path(home).expanduser().resolve()
        self._listener: multiprocessing.connection.Listener | None = None
        self._connection: multiprocessing.connection.Connection | None = None
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        if request_timeout_seconds <= 0:
            raise ValueError("request timeout must be positive")
        self._transport = _Transport(self, request_timeout_seconds)
        self._socket_path: Path | None = None

    @property
    def paired(self) -> bool:
        return load_or_create_pairing_key(self.home) is not None

    def start(self) -> None:
        """Accept the Chrome host only after the owner has explicitly paired this installation."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            try:
                key = load_or_create_pairing_key(self.home)
            except (OSError, ValueError) as exc:
                _log.warning("Chrome browser link not started: %s", exc)
                return
            if key is None:
                return
            address, family = endpoint(self.home)
            if family == "AF_UNIX":
                path = Path(address)
                path.parent.mkdir(parents=True, exist_ok=True)
                with contextlib.suppress(OSError):
                    path.parent.chmod(0o700)
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
                self._socket_path = path
            try:
                listener = multiprocessing.connection.Listener(address, family=family, authkey=key)
            except PermissionError:
                # Another instance or a process occupying the pipe name can own this endpoint.
                _log.warning("Chrome browser link endpoint is already in use: %s", address)
                return
            except OSError as exc:
                _log.warning("Chrome browser link endpoint could not be opened: %s", exc)
                return
            self._listener = listener
            self._stopping.clear()
            self._thread = threading.Thread(target=self._accept, name="chimera-browser-link", daemon=True)
            self._thread.start()

    def _accept(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection = cast(multiprocessing.connection.Connection, listener.accept())
            except multiprocessing.AuthenticationError:
                _log.warning("Chrome browser link rejected a host with an invalid authkey")
                continue
            except OSError:
                if not self._stopping.is_set():
                    time.sleep(0.1)
                continue
            with self._lock:
                if self._stopping.is_set():
                    connection.close()
                    return
                previous = self._connection
                self._connection = connection
            if previous is not None:
                with contextlib.suppress(OSError):
                    previous.close()
            # The thread remains the one acceptor; a new host replaces the old one.

    def connected(self) -> bool:
        with self._lock:
            connection = self._connection
            return connection is not None and not self._stopping.is_set()

    def transport(self) -> Any:
        return self._transport

    def _connection_snapshot(self) -> multiprocessing.connection.Connection | None:
        with self._lock:
            if self._stopping.is_set():
                return None
            return self._connection

    def _drop(self, connection: multiprocessing.connection.Connection) -> None:
        with self._lock:
            if self._connection is connection:
                self._connection = None
        with contextlib.suppress(OSError):
            connection.close()

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            listener, connection = self._listener, self._connection
            self._listener = None
            self._connection = None
            thread = self._thread
            self._thread = None
        for item in (connection, listener):
            if item is not None:
                with contextlib.suppress(OSError):
                    item.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        if self._socket_path is not None:
            with contextlib.suppress(FileNotFoundError):
                self._socket_path.unlink()
            self._socket_path = None

    close = stop

    def unpair(self) -> bool:
        self.stop()
        return unpair(self.home)
