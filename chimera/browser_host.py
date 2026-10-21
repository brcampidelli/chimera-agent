"""Chrome Native Messaging framing and the host-to-desktop forwarding entry point."""

from __future__ import annotations

import json
import multiprocessing.connection
import queue
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
            serve(input_stream, output_stream, _unpaired_handler)
            return
        if not reader_started:
            threading.Thread(
                target=read_extension, name="chimera-chrome-stdin", daemon=True
            ).start()
            reader_started = True
        address, family = endpoint(home)
        try:
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
                    write_message(output_stream, cast(dict[str, object], message))
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
