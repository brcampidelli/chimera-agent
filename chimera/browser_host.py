"""Chrome Native Messaging framing and the development host entry point.

The framing is complete and independently testable. Pairing and forwarding into a running desktop
app are intentionally represented by an injected handler until that app-side channel exists.
"""

from __future__ import annotations

import json
import struct
import sys
from collections.abc import Callable
from typing import BinaryIO, cast

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


def main() -> None:
    serve(sys.stdin.buffer, sys.stdout.buffer, _unpaired_handler)


if __name__ == "__main__":
    main()
