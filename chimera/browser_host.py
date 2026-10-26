"""Chrome Native Messaging framing and the host-to-desktop forwarding entry point."""

from __future__ import annotations

import json
import multiprocessing.connection
import ntpath
import os
import queue
import re
import struct
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast

from chimera.browser_link import (
    MAX_LINK_MESSAGE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_SECONDS,
    endpoint,
    load_or_create_pairing_key,
)

MAX_INPUT_MESSAGE_BYTES = 32 * 1024 * 1024
MAX_MESSAGE_BYTES = 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
MessageHandler = Callable[[dict[str, object]], dict[str, object]]


EXPECTED_EXE_ENV = "CHIMERA_BROWSER_HOST_EXPECTED_EXE"
EXPECTED_SERVICE_ROOT_ENV = "CHIMERA_BROWSER_HOST_EXPECTED_SERVICE_ROOT"
SERVICE_EXE_NAME = "chimera-service.exe"
# The version folder the desktop app copies its service into: service-bin\<semver>\
_SERVICE_VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?")


def _server_executable_matches(actual: str, expected: str) -> bool:
    """Reject a pipe server unless its canonical image path matches the installed host."""
    return os.path.normcase(os.path.realpath(actual)) == os.path.normcase(os.path.realpath(expected))


def _installed_service_image_matches(actual: str, service_root: str) -> bool:
    """Accept <service_root>\\<semver>\\chimera-service.exe and nothing else under the root.

    The desktop app runs its service from a copy in a version folder, and the folder name changes on
    every update, so pinning one version would break pairing after each one. The rule is exact about
    the shape instead: the root must match as a whole path component (so service-bin-evil is not
    inside service-bin), exactly two components may follow it (a version folder and the executable's
    name), and the version must look like a version. Both paths are normalised first, so ".." cannot
    leave the root. The caller passes resolved paths, so junctions and symlinks are already followed.
    The root is the user's own %APPDATA% folder recorded at registration, so only same-user placement
    is admitted, which is the threat model docs/chrome-extension.md already excludes.
    """
    image = ntpath.normcase(ntpath.normpath(actual))
    root = ntpath.normcase(ntpath.normpath(service_root)).rstrip("\\")
    if not root:
        return False
    prefix = root + "\\"
    if not image.startswith(prefix):
        return False
    parts = image[len(prefix):].split("\\")
    if len(parts) != 2:
        return False
    version, name = parts
    return _SERVICE_VERSION.fullmatch(version) is not None and name == SERVICE_EXE_NAME.lower()


def _registered_manifest_value(key: str) -> str | None:
    """Read one expectation recorded by --apply in the installed manifest."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        key_path = r"HKCU\Software\Google\Chrome\NativeMessagingHosts\app.chimera.browser"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as registry_key:
            manifest_path, _kind = winreg.QueryValueEx(registry_key, "")
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        value = payload.get(key)
        return value if isinstance(value, str) and value else None
    except (OSError, ValueError, TypeError):
        return None


def _registered_server_executable() -> str | None:
    """The exact server image recorded by --apply (dev source installs)."""
    return _registered_manifest_value("expected_server_executable")


def _registered_service_root() -> str | None:
    """The service-bin folder recorded by --apply for an installed desktop build."""
    return _registered_manifest_value("expected_service_root")


def _server_executable_for_handle(handle: object, accept: Callable[[str], bool]) -> bool:
    """Compare the server image on a connected named-pipe handle against `accept`."""
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetNamedPipeServerProcessId.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)
    ]
    api.GetNamedPipeServerProcessId.restype = wintypes.BOOL
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    api.QueryFullProcessImageNameW.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    server_pid = wintypes.ULONG()
    if not api.GetNamedPipeServerProcessId(handle, ctypes.byref(server_pid)):
        return False
    process = api.OpenProcess(0x1000, False, server_pid.value)
    if not process:
        return False
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not api.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)):
            return False
        return accept(buffer.value)
    finally:
        api.CloseHandle(process)


def _pipe_server_verified(connection: object) -> bool:
    """Whether a named-pipe peer is the registered Chimera image; non-pipe peers are not checked here.

    Fails closed: with no recorded expectation, or when the pipe does not identify its server, the
    peer is refused. The environment (written by the wrapper) takes precedence over the manifest.
    """
    expected_exe = os.environ.get(EXPECTED_EXE_ENV)
    service_root = os.environ.get(EXPECTED_SERVICE_ROOT_ENV)
    if expected_exe is None and service_root is None:
        expected_exe = _registered_server_executable()
        service_root = _registered_service_root()
    handle = getattr(connection, "_handle", None)
    if handle is None or not (expected_exe or service_root):
        return False

    # An empty value is no expectation: realpath("") would resolve to the working directory.
    root = service_root or None

    def accept(actual: str) -> bool:
        image = os.path.realpath(actual)
        if expected_exe and _server_executable_matches(image, expected_exe):
            return True
        return root is not None and _installed_service_image_matches(
            image, os.path.realpath(root)
        )

    return _server_executable_for_handle(handle, accept)


class _InvalidFrame(ValueError):
    pass


class _PartialFrame(ValueError):
    pass


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(min(remaining, _READ_CHUNK_BYTES))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: BinaryIO) -> dict[str, object] | None:
    """Read one frame; return None for clean EOF before a header."""
    header = _read_exact(stream, 4)
    if not header:
        return None
    if len(header) != 4:
        raise _PartialFrame("incomplete native messaging header")
    length = struct.unpack("<I", header)[0]
    if length > MAX_INPUT_MESSAGE_BYTES:
        remaining = length
        while remaining:
            chunk = stream.read(min(remaining, _READ_CHUNK_BYTES))
            if not chunk:
                raise _PartialFrame("incomplete oversized native messaging payload")
            remaining -= len(chunk)
        raise _InvalidFrame("message too large")
    payload = _read_exact(stream, length)
    if len(payload) != length:
        raise _PartialFrame("incomplete native messaging payload")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _InvalidFrame("invalid JSON") from exc
    if not isinstance(value, dict):
        raise _InvalidFrame("JSON value is not an object")
    return cast(dict[str, object], value)


def write_message(stream: BinaryIO, message: dict[str, object]) -> None:
    """Write one UTF-8 frame, rejecting oversized replies before emitting a header."""
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("response too large")
    stream.write(struct.pack("<I", len(payload)))
    stream.write(payload)
    stream.flush()


def serve(input_stream: BinaryIO, output_stream: BinaryIO, handler: MessageHandler) -> None:
    """Serve until EOF; malformed complete frames get a fixed error and do not break the loop."""
    while True:
        try:
            message = read_message(input_stream)
        except _InvalidFrame:
            write_message(output_stream, {"ok": False, "error": "invalid message"})
            continue
        except _PartialFrame as exc:
            print(f"Chrome Native Messaging closed on a partial frame: {exc}", file=sys.stderr)
            return
        if message is None:
            return
        try:
            reply = handler(message)
        except Exception:  # noqa: BLE001 — never forward page/error text across the protocol
            reply = {"id": message.get("id"), "ok": False, "error": "request failed"}
        try:
            write_message(output_stream, reply)
        except (TypeError, ValueError, OverflowError):
            fallback: dict[str, object] = {"ok": False, "error": "response too large or invalid"}
            if "id" in reply:
                fallback["id"] = reply["id"]
            write_message(output_stream, fallback)


def _unpaired_handler(message: dict[str, object]) -> dict[str, object]:
    """Fail closed until the desktop app's authenticated pairing/forwarding channel is implemented."""
    return {
        "id": message.get("id"),
        "ok": False,
        "error": "Chrome host is not paired with a running Chimera desktop app",
    }

def _forward_host(
    home: Path,
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    retry: bool = True,
) -> None:
    """Bridge app requests to Native Messaging without deserializing peer-controlled pickle."""
    replies: queue.Queue[dict[str, object] | None] = queue.Queue()
    stdin_closed = threading.Event()

    def read_extension() -> None:
        try:
            while True:
                message = read_message(input_stream)
                if message is None:
                    break
                replies.put(message)
        except (OSError, ValueError):
            pass
        finally:
            stdin_closed.set()
            replies.put(None)

    reader_started = False
    while True:
        try:
            key = load_or_create_pairing_key(home)
        except (OSError, ValueError):
            key = None
        if key is None:
            if not retry:
                serve(input_stream, output_stream, _unpaired_handler)
                return
            if not reader_started:
                threading.Thread(
                    target=read_extension, name="chimera-chrome-stdin", daemon=True
                ).start()
                reader_started = True
            address, family = endpoint(home)
            try:
                connection = multiprocessing.connection.Client(address, family=family)
            except (OSError, ConnectionError):
                if stdin_closed.is_set():
                    return
                if not retry:
                    serve(input_stream, output_stream, _unpaired_handler)
                    return
                time.sleep(RETRY_SECONDS)
                continue
            # The code travels in clear in this mode, so an intruding pipe instance must not get it.
            if family == "AF_PIPE" and not _pipe_server_verified(connection):
                connection.close()
                if not retry:
                    serve(input_stream, output_stream, _unpaired_handler)
                    return
                time.sleep(RETRY_SECONDS)
                continue
            try:
                while True:
                    try:
                        message = replies.get(timeout=0.1)
                    except queue.Empty:
                        if load_or_create_pairing_key(home) is not None:
                            break
                        if stdin_closed.is_set():
                            return
                        continue
                    if message is None:
                        return
                    if message.get("op") != "pair" or not isinstance(message.get("code"), str):
                        write_message(output_stream, {"id": message.get("id"), "ok": False, "error": "pairing required"})
                        continue
                    if load_or_create_pairing_key(home) is not None:
                        break
                    payload = json.dumps({"op": "pair", "code": message["code"]}, separators=(",", ":")).encode()
                    connection.send_bytes(payload)
                    if not connection.poll(REQUEST_TIMEOUT_SECONDS):
                        write_message(output_stream, {"id": message.get("id"), "ok": False, "error": "pairing unavailable"})
                        continue
                    response = json.loads(connection.recv_bytes(maxlength=4096).decode("utf-8"))
                    write_message(output_stream, {"id": message.get("id"), "ok": response.get("ok") is True, "result": response})
            except (EOFError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                return
            finally:
                connection.close()
            continue
        if not reader_started:
            threading.Thread(
                target=read_extension, name="chimera-chrome-stdin", daemon=True
            ).start()
            reader_started = True
        address, family = endpoint(home)
        try:
            if family == "AF_PIPE":
                connection = multiprocessing.connection.Client(address, family=family)
                if not _pipe_server_verified(connection):
                    connection.close()
                    if not retry:
                        serve(input_stream, output_stream, _unpaired_handler)
                        return
                    time.sleep(RETRY_SECONDS)
                    continue
                multiprocessing.connection.answer_challenge(connection, key)
                multiprocessing.connection.deliver_challenge(connection, key)
            else:
                connection = multiprocessing.connection.Client(address, family=family, authkey=key)
        except (OSError, ConnectionError, multiprocessing.AuthenticationError):
            if stdin_closed.is_set():
                return
            if not retry:
                serve(input_stream, output_stream, _unpaired_handler)
                return
            time.sleep(RETRY_SECONDS)
            continue
        try:
            while True:
                if stdin_closed.is_set():
                    return
                if not connection.poll(0.2):
                    continue
                try:
                    message = json.loads(
                        connection.recv_bytes(maxlength=MAX_LINK_MESSAGE_BYTES).decode("utf-8")
                    )
                except (EOFError, OSError, UnicodeDecodeError, json.JSONDecodeError):
                    break
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                try:
                    write_message(output_stream, message)
                except (OSError, ValueError):
                    return
                deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
                reply: dict[str, object] | None = None
                while time.monotonic() < deadline:
                    try:
                        candidate = replies.get(
                            timeout=max(0.0, deadline - time.monotonic())
                        )
                    except queue.Empty:
                        break
                    if candidate is None:
                        return
                    if candidate.get("id") == request_id:
                        reply = candidate
                        break
                if reply is None:
                    continue
                payload = json.dumps(reply, separators=(",", ":")).encode("utf-8")
                if len(payload) > MAX_LINK_MESSAGE_BYTES:
                    break
                try:
                    connection.send_bytes(payload)
                except (OSError, EOFError):
                    break
        finally:
            connection.close()
        if stdin_closed.is_set():
            return
        if not retry:
            serve(input_stream, output_stream, _unpaired_handler)
            return
        time.sleep(RETRY_SECONDS)


def main() -> None:
    from chimera.config import get_settings

    _forward_host(Path(get_settings().home), sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    main()
