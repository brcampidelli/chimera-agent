"""Binary payloads an MCP tool returns go to a file, and the model reads a short placeholder.

The case measured on 2026-10-27 with Chimera 0.79.0: a VoiceStudio ``generate_speech`` answer carried
a 230 KB WAV as base64, inside a JSON text block and again in ``structuredContent``. Both reached the
model whole: 910 000 prompt tokens, US$ 0.11, a turn stopped with ``context_stuck`` after a
compaction, and the person never received the audio.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.integrations.mcp_client import (
    DEFAULT_MAX_OUTPUT_CHARS,
    StdioMCPSession,
    StreamableHTTPMCPSession,
    _content_to_text,
)
from chimera.integrations.mcp_payloads import MIN_PAYLOAD_CHARS, PayloadSink, default_sink
from chimera.tools.write_region import WriteRegion

#: A RIFF/WAVE file of about 48 KB: enough that its base64 is far past the payload threshold.
WAV = b"RIFF" + (48_128).to_bytes(4, "little") + b"WAVE" + bytes(range(256)) * 188
#: A PNG-signed image of about 3 KB.
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 12


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text, annotations=None, meta=None)


def _block(kind: str, mime: str, data: str) -> SimpleNamespace:
    return SimpleNamespace(type=kind, data=data, mimeType=mime, annotations=None, meta=None)


def _result(*blocks: Any, structured: Any = None) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), structuredContent=structured, isError=False)


def _sink(tmp_path: Path, region: WriteRegion | None = None) -> PayloadSink:
    """A sink whose root is ``tmp_path/out``, inside a project that is ``tmp_path``."""
    return PayloadSink(root=tmp_path / "out", workspace=tmp_path, write_region=region)


def _files(root: Path) -> list[Path]:
    return sorted(root.iterdir()) if root.exists() else []


def test_wav_base64_in_a_json_text_block_becomes_a_wav_file(tmp_path: Path) -> None:
    payload = json.dumps({"wav_base64": _b64(WAV), "audio_id": "a1", "audio_duration_s": 4.81})

    out = _content_to_text(_result(_text(payload)), _sink(tmp_path))

    saved = _files(tmp_path / "out")
    assert len(saved) == 1
    assert saved[0].suffix == ".wav"
    assert saved[0].read_bytes() == WAV
    answer = json.loads(out)
    assert answer["audio_id"] == "a1" and answer["audio_duration_s"] == 4.81
    assert answer["wav_base64"] == f"<saved: {saved[0]}, {len(WAV)} bytes, audio/wav>"
    assert len(out) < 1_000  # the model reads a path, not 64 000 characters of base64


def test_an_audio_block_with_data_becomes_a_file(tmp_path: Path) -> None:
    out = _content_to_text(_result(_block("audio", "audio/wav", _b64(WAV))), _sink(tmp_path))

    saved = _files(tmp_path / "out")
    assert len(saved) == 1 and saved[0].read_bytes() == WAV
    assert out == f"[audio audio/wav, {len(WAV)} bytes] <saved: {saved[0]}>"


def test_an_image_block_with_data_becomes_a_png_file(tmp_path: Path) -> None:
    out = _content_to_text(_result(_block("image", "image/png", _b64(PNG))), _sink(tmp_path))

    saved = _files(tmp_path / "out")
    assert len(saved) == 1 and saved[0].suffix == ".png" and saved[0].read_bytes() == PNG
    assert out.startswith(f"[image image/png, {len(PNG)} bytes] <saved: ")


def test_a_blob_resource_becomes_a_file_and_keeps_its_uri(tmp_path: Path) -> None:
    resource = SimpleNamespace(uri="file:///take/1.wav", mimeType="audio/wav", blob=_b64(WAV))
    out = _content_to_text(
        _result(SimpleNamespace(type="resource", resource=resource)), _sink(tmp_path)
    )

    saved = _files(tmp_path / "out")
    assert len(saved) == 1 and saved[0].read_bytes() == WAV
    assert "file:///take/1.wav" in out
    assert str(saved[0]) in out


def test_the_same_payload_in_text_and_structured_content_is_stored_once(tmp_path: Path) -> None:
    answer = {"wav_base64": _b64(WAV), "audio_id": "a1"}

    out = _content_to_text(
        _result(_text(json.dumps(answer)), structured=answer), _sink(tmp_path)
    )

    assert len(_files(tmp_path / "out")) == 1
    assert out.count("<saved:") == 1  # the structured copy is not appended a second time


def test_a_payload_only_in_structured_content_is_stored_too(tmp_path: Path) -> None:
    out = _content_to_text(_result(structured={"wav_base64": _b64(WAV)}), _sink(tmp_path))

    assert len(_files(tmp_path / "out")) == 1
    assert "<saved:" in out and _b64(WAV)[:40] not in out


def test_plain_text_is_returned_untouched_and_writes_nothing(tmp_path: Path) -> None:
    prose = ("The quick brown fox jumps over the lazy dog. " * 300).strip()
    assert len(prose) > MIN_PAYLOAD_CHARS

    out = _content_to_text(_result(_text(prose)), _sink(tmp_path))

    assert out == prose
    assert _files(tmp_path / "out") == []


def test_a_short_base64_value_is_not_touched(tmp_path: Path) -> None:
    answer = {"token_base64": "aGVsbG8=", "data": "aGk="}
    text = json.dumps(answer, indent=2)

    out = _content_to_text(_result(_text(text)), _sink(tmp_path))

    assert out == text
    assert _files(tmp_path / "out") == []


def test_a_long_hex_identifier_is_not_written_out_as_a_file(tmp_path: Path) -> None:
    # Hex is valid base64 and decodes, and it is long. Without a known file type it is still a hash.
    text = json.dumps({"sha": "ab" * (MIN_PAYLOAD_CHARS // 2 + 8)})

    out = _content_to_text(_result(_text(text)), _sink(tmp_path))

    assert out == text
    assert _files(tmp_path / "out") == []


def test_unknown_bytes_under_a_key_that_does_not_name_a_payload_are_left_alone(tmp_path: Path) -> None:
    text = json.dumps({"metadata": _b64(bytes(range(256)) * 40)})

    out = _content_to_text(_result(_text(text)), _sink(tmp_path))

    assert out == text
    assert _files(tmp_path / "out") == []


def test_unknown_bytes_under_a_payload_key_are_stored_as_bin(tmp_path: Path) -> None:
    text = json.dumps({"blob_base64": _b64(bytes(range(256)) * 40)})

    out = _content_to_text(_result(_text(text)), _sink(tmp_path))

    saved = _files(tmp_path / "out")
    assert len(saved) == 1 and saved[0].suffix == ".bin"
    assert "<saved:" in out


def test_a_text_answer_that_is_json_without_payloads_keeps_its_spacing(tmp_path: Path) -> None:
    text = json.dumps({"rows": [{"id": i, "label": "x" * 20} for i in range(300)]}, indent=3)
    assert len(text) > MIN_PAYLOAD_CHARS

    assert _content_to_text(_result(_text(text)), _sink(tmp_path)) == text


def test_an_svg_that_does_not_sanitise_never_reaches_disk(tmp_path: Path) -> None:
    script = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

    out = _content_to_text(
        _result(_block("image", "image/svg+xml", _b64(script))), _sink(tmp_path)
    )

    for saved in _files(tmp_path / "out"):
        assert b"<script" not in saved.read_bytes()
    assert b"<script" not in out.encode()


def test_a_write_region_that_refuses_the_folder_refuses_the_payload(tmp_path: Path) -> None:
    region = WriteRegion(["docs/**"], tmp_path)
    payload = json.dumps({"wav_base64": _b64(WAV)})

    out = _content_to_text(_result(_text(payload)), _sink(tmp_path, region))

    assert _files(tmp_path / "out") == []
    assert "<not saved:" in out


def test_a_write_region_that_allows_the_folder_stores_the_payload(tmp_path: Path) -> None:
    region = WriteRegion(["out/**"], tmp_path)

    _content_to_text(_result(_block("audio", "audio/wav", _b64(WAV))), _sink(tmp_path, region))

    assert len(_files(tmp_path / "out")) == 1


def test_a_folder_inside_the_projects_chimera_directory_is_refused(tmp_path: Path) -> None:
    # .chimera holds the audit log and the receipts; a run must not write its own evidence.
    sink = PayloadSink(root=tmp_path / ".chimera" / "mcp-output", workspace=tmp_path)

    out = _content_to_text(_result(_block("audio", "audio/wav", _b64(WAV))), sink)

    assert not (tmp_path / ".chimera").exists()
    assert "<saved:" not in out


def test_the_default_folder_is_the_apps_own_mcp_output(tmp_path: Path) -> None:
    from chimera.config import get_settings

    sink = default_sink()

    assert sink.root == Path(get_settings().home) / "mcp-output"


def test_the_default_output_cap_is_documented_and_applied_when_nothing_is_configured() -> None:
    assert DEFAULT_MAX_OUTPUT_CHARS == 60_000
    assert StdioMCPSession("unused").max_output_chars == DEFAULT_MAX_OUTPUT_CHARS
    assert StreamableHTTPMCPSession("http://localhost").max_output_chars == DEFAULT_MAX_OUTPUT_CHARS


class _FakeClientSession:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self._result


def _answering(session: Any, result: Any) -> tuple[Any, asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    session._loop = loop
    session._session = _FakeClientSession(result)
    return session, loop


def test_a_very_long_text_answer_is_capped_by_default_and_says_so() -> None:
    session, loop = _answering(StdioMCPSession("unused"), _result(_text("a" * 70_000)))
    try:
        out = session.call_tool("report", {})
    finally:
        loop.call_soon_threadsafe(loop.stop)

    assert len(out) < 70_000
    assert f"[truncated, 70000 chars total; limit {DEFAULT_MAX_OUTPUT_CHARS} chars]" in out


def test_a_session_writes_its_payloads_to_the_sink_it_was_given(tmp_path: Path) -> None:
    session, loop = _answering(
        StdioMCPSession("unused", payload_sink=_sink(tmp_path)),
        _result(_text(json.dumps({"wav_base64": _b64(WAV), "audio_id": "a1"}))),
    )
    try:
        out = session.call_tool("generate_speech", {"text": "oi"})
    finally:
        loop.call_soon_threadsafe(loop.stop)

    saved = _files(tmp_path / "out")
    assert len(saved) == 1 and saved[0].read_bytes() == WAV
    # The path is JSON-escaped inside the answer on Windows, so the file's name is what is checked.
    assert saved[0].name in out
    assert _b64(WAV)[:40] not in out


@pytest.mark.parametrize("kind", ["image", "audio"])
def test_a_block_whose_payload_does_not_decode_is_named_not_dumped(tmp_path: Path, kind: str) -> None:
    out = _content_to_text(_result(_block(kind, "audio/wav", "!!!not base64!!!")), _sink(tmp_path))

    assert "not decodable" in out
    assert _files(tmp_path / "out") == []
