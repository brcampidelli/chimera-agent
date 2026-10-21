"""Authenticated local link between Chimera and its Chrome Native Messaging host."""

from __future__ import annotations

import contextlib
import getpass
import hmac
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
from typing import Any, Protocol, cast

PAIRING_KEY_BYTES = 32
REQUEST_TIMEOUT_SECONDS = 20.0
RETRY_SECONDS = 2.0
MAX_LINK_MESSAGE_BYTES = 32 * 1024 * 1024
_log = logging.getLogger(__name__)


class _LinkConnection(Protocol):
    """What the link uses of a connection. `Listener.accept()` returns a `Connection` on POSIX and a
    `PipeConnection` on Windows, which the stubs do not relate; naming the four methods types both
    without a cast that is redundant on one platform and required on the other."""

    def send_bytes(self, buf: bytes) -> None: ...
    def recv_bytes(self, maxlength: int | None = None) -> bytes: ...
    def poll(self, timeout: float | None = 0.0) -> bool: ...
    def close(self) -> None: ...

PAIRING_VAULT_NAME = "CHIMERA_BROWSER_PAIRING"


def pairing_key_path(home: Path) -> Path:
    return home / "browser" / "pairing.key"


def pairing_key_store(home: Path) -> str:
    """Return the active store, migrating a legacy file key into an available vault."""
    from chimera import config_vault

    path = pairing_key_path(home)
    try:
        file_value = path.read_bytes()
    except FileNotFoundError:
        file_value = None
    if file_value is not None and len(file_value) != PAIRING_KEY_BYTES:
        raise ValueError("Chrome pairing key is invalid")
    if not config_vault.available():
        return "file"
    vaulted = config_vault.read(PAIRING_VAULT_NAME)
    if file_value is not None:
        encoded = file_value.hex()
        if vaulted is None:
            if not config_vault.store(PAIRING_VAULT_NAME, encoded):
                return "file"
        elif not hmac.compare_digest(vaulted, encoded):
            raise ValueError("Chrome pairing key conflicts with the OS vault")
        path.unlink()
    return "vault" if config_vault.read(PAIRING_VAULT_NAME) is not None else "file"


def load_or_create_pairing_key(home: Path, *, create: bool = False) -> bytes | None:
    """Read/create a pairing key, preferring the OS vault and migrating a legacy file."""
    from chimera import config_vault

    path = pairing_key_path(home)
    if pairing_key_store(home) == "vault":
        encoded = config_vault.read(PAIRING_VAULT_NAME)
        if encoded is not None:
            try:
                value = bytes.fromhex(encoded)
            except ValueError as exc:
                raise ValueError("Chrome pairing key is invalid") from exc
            if len(value) != PAIRING_KEY_BYTES:
                raise ValueError("Chrome pairing key is invalid")
            return value
    if create and config_vault.available():
        value = secrets.token_bytes(PAIRING_KEY_BYTES)
        if config_vault.store(PAIRING_VAULT_NAME, value.hex()):
            return value
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
    from chimera import config_vault

    removed = config_vault.forget(PAIRING_VAULT_NAME)
    path = pairing_key_path(home)
    try:
        path.unlink()
    except FileNotFoundError:
        return removed
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
        self._connection: _LinkConnection | None = None
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        if request_timeout_seconds <= 0:
            raise ValueError("request timeout must be positive")
        self._transport = _Transport(self, request_timeout_seconds)
        self._socket_path: Path | None = None
        self._pair_lock = threading.Lock()
        self._pair_code: str | None = None
        self._pair_expires = 0.0
        self._pair_attempts = 0
        self._pairing_active = False

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
            self._pairing_active = key is None
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

    def issue_pair_code(self) -> tuple[str, int]:
        if self.paired:
            raise ValueError("Chrome is already paired")
        with self._pair_lock:
            self._pair_code = f"{secrets.randbelow(1_000_000):06d}"
            self._pair_expires = time.monotonic() + 120
            self._pair_attempts = 0
            return self._pair_code, 120

    def pair_with_code(self, code: str) -> bool:
        with self._pair_lock:
            expected = self._pair_code
            if expected is None or time.monotonic() >= self._pair_expires or self._pair_attempts >= 5:
                self._pair_code = None
                return False
            self._pair_attempts += 1
            valid = len(code) == 6 and code.isascii() and code.isdigit() and hmac.compare_digest(code, expected)
            if not valid:
                if self._pair_attempts >= 5:
                    self._pair_code = None
                return False
            self._pair_code = None
        if load_or_create_pairing_key(self.home, create=True) is None:
            return False
        self._pairing_active = False
        threading.Thread(target=self._restart_paired_listener, daemon=True).start()
        return True

    def _restart_paired_listener(self) -> None:
        time.sleep(0.1)
        self.stop()
        self.start()

    def _accept(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection: _LinkConnection = listener.accept()
            except multiprocessing.AuthenticationError:
                _log.warning("Chrome browser link rejected a host with an invalid authkey")
                continue
            except OSError:
                if not self._stopping.is_set():
                    time.sleep(0.1)
                continue
            if self._pairing_active:
                try:
                    request = json.loads(connection.recv_bytes(maxlength=4096).decode("utf-8"))
                    accepted = (
                        isinstance(request, dict)
                        and request.get("op") == "pair"
                        and isinstance(request.get("code"), str)
                        and self.pair_with_code(request["code"])
                    )
                    connection.send_bytes(json.dumps({"ok": accepted}).encode("utf-8"))
                except (EOFError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    pass
                finally:
                    connection.close()
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

    def _connection_snapshot(self) -> _LinkConnection | None:
        with self._lock:
            if self._stopping.is_set():
                return None
            return self._connection

    def _drop(self, connection: _LinkConnection) -> None:
        with self._lock:
            if self._connection is connection:
                self._connection = None
        with contextlib.suppress(OSError):
            connection.close()

    def disconnect_host(self) -> None:
        """Drop the current extension connection without releasing the listening endpoint."""
        with self._lock:
            connection = self._connection
            self._connection = None
        if connection is not None:
            with contextlib.suppress(OSError):
                connection.close()

    def stop(self) -> None:
        self._stopping.set()
        self._pairing_active = False
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
        with self._pair_lock:
            self._pair_code = None
            self._pair_expires = 0.0
            self._pair_attempts = 0
        self.stop()
        return unpair(self.home)
