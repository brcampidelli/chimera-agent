from __future__ import annotations

import io
import json
import struct

import pytest

from chimera.browser_host import (
    MAX_INPUT_MESSAGE_BYTES,
    MAX_MESSAGE_BYTES,
    read_message,
    serve,
    write_message,
)


def _frame(value: object) -> bytes:
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def test_native_message_round_trip_utf8() -> None:
    stream = io.BytesIO()
    write_message(stream, {"id": 4, "text": "olá"})
    stream.seek(0)
    assert read_message(stream) == {"id": 4, "text": "olá"}
    assert read_message(stream) is None


def test_read_rejects_bad_or_non_object_frames() -> None:
    with pytest.raises(ValueError, match="header"):
        read_message(io.BytesIO(b"\x01"))
    with pytest.raises(ValueError, match="payload"):
        read_message(io.BytesIO(struct.pack("<I", 4) + b"{}"))
    with pytest.raises(ValueError, match="object"):
        read_message(io.BytesIO(_frame([])))


def test_read_rejects_oversized_length_before_payload() -> None:
    payload = b"x" * (MAX_INPUT_MESSAGE_BYTES + 1)
    framed = io.BytesIO(struct.pack("<I", len(payload)) + payload)
    with pytest.raises(ValueError, match="too large"):
        read_message(framed)
    assert framed.read() == b""


def test_serve_returns_error_and_continues() -> None:
    source = io.BytesIO(_frame({"id": 1}) + _frame({"id": 2}))
    target = io.BytesIO()
    serve(source, target, lambda message: (_ for _ in ()).throw(RuntimeError("no pair")) if message["id"] == 1 else {"id": 2, "ok": True})
    target.seek(0)
    assert read_message(target) == {"id": 1, "ok": False, "error": "request failed"}
    assert read_message(target) == {"id": 2, "ok": True}


def test_serve_bad_json_responds_and_continues() -> None:
    source = io.BytesIO(struct.pack("<I", 1) + b"{" + _frame({"id": 2}))
    target = io.BytesIO()
    serve(source, target, lambda message: {"id": message["id"], "ok": True, "result": "done"})
    target.seek(0)
    assert read_message(target) == {"ok": False, "error": "invalid message"}
    assert read_message(target) == {"id": 2, "ok": True, "result": "done"}


def test_serve_replaces_oversized_response_with_short_error() -> None:
    target = io.BytesIO()
    serve(io.BytesIO(_frame({"id": 9})), target, lambda _message: {"id": 9, "ok": True, "result": "x" * MAX_MESSAGE_BYTES})
    target.seek(0)
    assert read_message(target) == {"ok": False, "error": "response too large or invalid", "id": 9}


def test_input_over_limit_is_drained_then_loop_continues() -> None:
    oversized = b"x" * (MAX_INPUT_MESSAGE_BYTES + 1)
    source = io.BytesIO(
        struct.pack("<I", len(oversized)) + oversized + _frame({"id": 10})
    )
    target = io.BytesIO()
    serve(source, target, lambda message: {"id": message["id"], "ok": True})
    target.seek(0)
    assert read_message(target) == {"ok": False, "error": "invalid message"}
    assert read_message(target) == {"id": 10, "ok": True}


def test_registry_manifest_is_scoped_to_extension_id() -> None:
    from scripts.register_chrome_host import manifest

    value = manifest("a" * 32, "C:/Chimera/chimera-browser-host.cmd")
    assert value["allowed_origins"] == [f"chrome-extension://{'a' * 32}/"]
    assert value["type"] == "stdio"
    assert value["path"].endswith("chimera-browser-host.cmd")


def test_invalid_registry_ids_are_rejected_and_cmd_wrapper_is_described(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.register_chrome_host import main

    for extension_id in ("A" + "a" * 31, "q" * 32):
        with pytest.raises(SystemExit):
            import sys
            from unittest.mock import patch

            with patch.object(sys, "argv", ["register_chrome_host", extension_id]):
                main()
    valid_id = "a" * 32
    import sys
    from unittest.mock import patch

    with patch.object(sys, "argv", ["register_chrome_host", valid_id]):
        assert main() == 0
    printed = capsys.readouterr().out
    assert '"wrapper_path"' in printed
    assert '@echo off' in printed and ' -I -m chimera.browser_host' in printed
