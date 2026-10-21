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

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

PAIRING_KEY_BYTES = 32
REQUEST_TIMEOUT_SECONDS = 20.0
RETRY_SECONDS = 2.0
MAX_LINK_MESSAGE_BYTES = 32 * 1024 * 1024
#: How long a freshly accepted host may stay silent. The accept loop is one thread: a local process
#: that connects and says nothing would otherwise stall the link until the app restarts.
HANDSHAKE_TIMEOUT_SECONDS = 5.0
_log = logging.getLogger(__name__)

class _LinkConnection(Protocol):
    """What the link uses of a connection. `Listener.accept()` returns a `Connection` on POSIX and a
    `PipeConnection` on Windows, which the stubs do not relate; naming the four methods types both
    without a cast that is redundant on one platform and required on the other."""

    def send_bytes(self, buf: bytes) -> None: ...
    def recv_bytes(self, maxlength: int | None = None) -> bytes: ...
    def poll(self, timeout: float | None = 0.0) -> bool: ...
    def close(self) -> None: ...

class _PipeListener(Protocol):
    """Common listener surface for multiprocessing listeners and the secured Windows pipe."""

    def accept(self) -> _LinkConnection: ...
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


if os.name == "nt":
    _PIPE_ACCESS_DUPLEX = 0x00000003
    _FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _PIPE_TYPE_MESSAGE = 0x00000004
    _PIPE_READMODE_MESSAGE = 0x00000002
    _PIPE_WAIT = 0x00000000
    _PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
    _ERROR_ACCESS_DENIED = 5
    _ERROR_IO_PENDING = 997
    _ERROR_PIPE_CONNECTED = 535
    _ERROR_WAIT_TIMEOUT = 258
    _ACCEPT_WAIT_MILLISECONDS = 1000
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1

    _SECURITY_DESCRIPTOR_REVISION = 1

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _TokenUser(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    WinDLL = getattr(ctypes, "WinDLL")  # noqa: B009 — Win32-only ctypes APIs are absent from Linux stubs.
    WinError = getattr(ctypes, "WinError")  # noqa: B009 — Win32-only ctypes APIs are absent from Linux stubs.
    get_last_error = getattr(ctypes, "get_last_error")  # noqa: B009 — Win32-only ctypes APIs are absent from Linux stubs.

    _kernel32 = WinDLL("kernel32", use_last_error=True)
    _advapi32 = WinDLL("advapi32", use_last_error=True)
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL
    _kernel32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_SecurityAttributes),
    ]
    _kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    _kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
    _kernel32.ConnectNamedPipe.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(wintypes.DWORD), wintypes.BOOL
    ]
    _kernel32.GetOverlappedResult.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID
    ]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL

    def _current_user_sid() -> str:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise WinError(get_last_error())
        try:
            needed = wintypes.DWORD()
            _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            if not _advapi32.GetTokenInformation(
                token, _TOKEN_USER, buffer, needed, ctypes.byref(needed)
            ):
                raise WinError(get_last_error())
            user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
            sid_string = wintypes.LPWSTR()
            if not _advapi32.ConvertSidToStringSidW(user.Sid, ctypes.byref(sid_string)):
                raise WinError(get_last_error())
            try:
                return str(sid_string.value)
            finally:
                _kernel32.LocalFree(ctypes.cast(sid_string, wintypes.HLOCAL))
        finally:
            _kernel32.CloseHandle(token)

    def _windows_pipe_security_attributes() -> tuple[object, _SecurityAttributes]:
        """Build the owner-only DACL and keep its descriptor alive through pipe creation."""
        sid = _current_user_sid()
        descriptor = wintypes.LPVOID()
        if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;;GA;;;{sid})", _SECURITY_DESCRIPTOR_REVISION, ctypes.byref(descriptor), None
        ):
            raise WinError(get_last_error())
        attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        return descriptor, attributes

    class _WindowsPipeListener:
        """Single-instance, owner-only Windows pipe listener with multiprocessing framing."""

        def __init__(self, address: str) -> None:
            self._address = address
            self._closed = False
            self._accept_handle = 0
            self._next_handle = self._new_handle(first=True)

        def _new_handle(self, *, first: bool) -> int:
            descriptor, attributes = _windows_pipe_security_attributes()
            try:
                flags = _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED
                if first:
                    flags |= _FILE_FLAG_FIRST_PIPE_INSTANCE
                mode = (
                    _PIPE_TYPE_MESSAGE
                    | _PIPE_READMODE_MESSAGE
                    | _PIPE_WAIT
                    | _PIPE_REJECT_REMOTE_CLIENTS
                )
                handle = _kernel32.CreateNamedPipeW(
                    self._address, flags, mode, 255, 65536, 65536, 0, ctypes.byref(attributes)
                )
                if handle == wintypes.HANDLE(-1).value:
                    error = get_last_error()
                    if first and error == _ERROR_ACCESS_DENIED:
                        _log.warning(
                            "Chrome browser link pipe name is already occupied: %s", self._address
                        )
                    raise WinError(error)
                return int(handle)
            finally:
                _kernel32.LocalFree(descriptor)

        def accept(self) -> _LinkConnection:
            if self._closed:
                raise OSError("listener is closed")
            if not self._next_handle:
                self._next_handle = self._new_handle(first=False)
            handle, self._next_handle = self._next_handle, 0
            self._accept_handle = handle
            event = _kernel32.CreateEventW(None, True, False, None)
            if not event:
                error = get_last_error()
                _kernel32.CloseHandle(handle)
                self._accept_handle = 0
                if not self._closed:
                    self._next_handle = self._new_handle(first=False)
                raise WinError(error)
            overlapped = _Overlapped()
            overlapped.hEvent = event
            try:
                connected = _kernel32.ConnectNamedPipe(handle, ctypes.byref(overlapped))
                if not connected:
                    error = get_last_error()
                    if error == _ERROR_IO_PENDING:
                        result = _kernel32.WaitForSingleObject(event, _ACCEPT_WAIT_MILLISECONDS)
                        if result == _ERROR_WAIT_TIMEOUT:
                            raise TimeoutError("timed out waiting for named-pipe client")
                        transferred = wintypes.DWORD()
                        if result != 0 or not _kernel32.GetOverlappedResult(
                            handle, ctypes.byref(overlapped), ctypes.byref(transferred), True
                        ):
                            raise WinError(get_last_error())
                    elif error != _ERROR_PIPE_CONNECTED:
                        raise WinError(error)
                self._next_handle = self._new_handle(first=False)
            except BaseException:
                _kernel32.CloseHandle(handle)
                self._accept_handle = 0
                if not self._closed:
                    self._next_handle = self._new_handle(first=False)
                raise
            finally:
                _kernel32.CloseHandle(event)
            self._accept_handle = 0
            pipe_connection = getattr(multiprocessing.connection, "PipeConnection")  # noqa: B009 — available only on Windows.
            return cast(_LinkConnection, pipe_connection(handle))

        def close(self) -> None:
            self._closed = True
            if self._accept_handle:
                _kernel32.CloseHandle(self._accept_handle)
                self._accept_handle = 0
            if self._next_handle:
                _kernel32.CloseHandle(self._next_handle)
                self._next_handle = 0

    def _windows_pipe_listener(address: str) -> _PipeListener:
        return _WindowsPipeListener(address)
else:
    def _windows_pipe_listener(address: str) -> _PipeListener:
        raise OSError("Windows named pipes are not available on this platform")


def _handshake_within(connection: _LinkConnection, key: bytes, timeout: float) -> bool:
    """Run the mutual HMAC challenge, giving up on a peer that stays silent.

    The server speaks first, so there is nothing to `poll` for before it; the wait is inside
    `deliver_challenge`. It runs on its own thread so the single accept loop can walk away, and
    closing the connection ends the stuck read. Raises `AuthenticationError` for a wrong key.
    """
    failure: list[BaseException] = []

    def run() -> None:
        try:
            secured = cast(Any, connection)
            multiprocessing.connection.deliver_challenge(secured, key)
            multiprocessing.connection.answer_challenge(secured, key)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the accept thread below
            failure.append(exc)

    worker = threading.Thread(target=run, name="chimera-browser-handshake", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        with contextlib.suppress(OSError):
            connection.close()
        return False
    if failure:
        raise failure[0]
    return True


class BrowserLink:
    """A single-host authenticated listener; transports are safe to share across browser calls."""

    def __init__(self, home: Path, *, request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS) -> None:
        self.home = Path(home).expanduser().resolve()
        self._listener: _PipeListener | None = None
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
        # Set by `shutdown`: an unpair finishing after the app's shutdown event must not reopen the pipe.
        self._closed = False

    @property
    def paired(self) -> bool:
        return load_or_create_pairing_key(self.home) is not None

    def start(self) -> None:
        """Accept the Chrome host only after the owner has explicitly paired this installation."""
        with self._lock:
            if self._closed or (self._thread is not None and self._thread.is_alive()):
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
                if family == "AF_PIPE":
                    listener = _windows_pipe_listener(address)
                else:
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

    def listening_for_pairing(self) -> bool:
        with self._lock:
            thread = self._thread
            return self._pairing_active and thread is not None and thread.is_alive()

    def issue_pair_code(self) -> tuple[str, int]:
        if self.paired:
            raise ValueError("Chrome is already paired")
        if not self.listening_for_pairing():
            # A code nobody is listening for is a dead end on screen; say so instead.
            raise RuntimeError("Chrome link is not listening")
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
            # `stop` waits 2 s for this thread; a handshake may take 5. If `start` already put a new
            # acceptor on a new listener, the old one leaves instead of sharing it (E5 re-review).
            if threading.current_thread() is not self._thread:
                return
            listener = self._listener
            if listener is None:
                return
            try:
                connection: _LinkConnection = listener.accept()
                if os.name == "nt" and not self._pairing_active:
                    key = load_or_create_pairing_key(self.home)
                    if key is None:
                        connection.close()
                        continue
                    if not _handshake_within(connection, key, HANDSHAKE_TIMEOUT_SECONDS):
                        continue
            except multiprocessing.AuthenticationError:
                _log.warning("Chrome browser link rejected a host with an invalid authkey")
                with contextlib.suppress(OSError):
                    connection.close()
                continue
            except EOFError:
                # A peer that read the challenge and hung up (Chrome killing the host mid-handshake,
                # or any local process). `EOFError` is not an `OSError`; uncaught it ended the thread
                # and the link stayed dead until the app restarted (E5 re-review).
                with contextlib.suppress(OSError):
                    connection.close()
                continue
            except OSError:
                if not self._stopping.is_set():
                    time.sleep(0.1)
                continue
            if self._pairing_active:
                try:
                    if not connection.poll(HANDSHAKE_TIMEOUT_SECONDS):
                        continue
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
                if self._stopping.is_set() or threading.current_thread() is not self._thread:
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

    def shutdown(self) -> None:
        """Stop for good: what the app's shutdown event calls, so nothing reopens the pipe after it."""
        self._closed = True
        self.stop()

    close = shutdown

    def unpair(self) -> bool:
        with self._pair_lock:
            self._pair_code = None
            self._pair_expires = 0.0
            self._pair_attempts = 0
        self.stop()
        removed = unpair(self.home)
        # Back to pairing mode at once: without this the screen offered a code no pipe was hearing
        # until the app restarted (E5 review).
        self.start()
        return removed
