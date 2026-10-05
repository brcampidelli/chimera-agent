"""An MCP tool's non-text content reached the model as a pydantic repr, base64 and all.

``_content_to_text`` took ``block.text`` when a block had one and ``str(block)`` otherwise. Only
``TextContent`` has a ``text``. An ``ImageContent`` therefore entered the context as
``type='image' data='iVBORw0KGgo…' mimeType='image/png' annotations=None meta=None`` — the whole
base64 payload, which is tokens the model cannot read and a screenshot can be hundreds of thousands
of them. An ``EmbeddedResource`` came through the same way, its text buried in a repr next to fields
nobody asked for, and a blob resource carried its base64 too. ``structuredContent``, which a server
may send INSTEAD of a text block, was never read: such a tool answered with an empty string.

Study 30, S30-10. The fakes below carry the field names of ``mcp.types`` 1.x (``ImageContent``:
type, data, mimeType; ``EmbeddedResource``: type, resource; ``TextResourceContents``: uri, mimeType,
text; ``BlobResourceContents``: uri, mimeType, blob; ``ResourceLink``: type, uri, name, mimeType;
``CallToolResult``: content, structuredContent, isError), checked against the installed SDK, because
the optional ``mcp`` package is not installed where the suite runs.
"""

from __future__ import annotations

import asyncio
import base64
import threading
from types import SimpleNamespace
from typing import Any

from chimera.integrations.mcp_client import StdioMCPSession, _content_to_text

#: 3 000 bytes of something that is not text. Its base64 is the thing that must not reach the model.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 11 + b"\x00" * 176
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text, annotations=None, meta=None)


def _image(data: str = _PNG_B64, mime: str = "image/png") -> SimpleNamespace:
    return SimpleNamespace(type="image", data=data, mimeType=mime, annotations=None, meta=None)


def _result(*blocks: Any, structured: Any = None, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), structuredContent=structured, isError=is_error)


def test_an_image_block_reaches_the_model_as_a_typed_placeholder_without_its_base64() -> None:
    out = _content_to_text(_result(_text("here is the chart"), _image()))

    assert "here is the chart" in out
    assert _PNG_B64[:40] not in out
    assert f"[image image/png, {len(_PNG_BYTES)} bytes]" in out
    assert "annotations=" not in out  # no repr of any kind


def test_an_audio_block_is_a_placeholder_too() -> None:
    audio = SimpleNamespace(type="audio", data=_PNG_B64, mimeType="audio/wav", annotations=None)
    out = _content_to_text(_result(audio))

    assert out == f"[audio audio/wav, {len(_PNG_BYTES)} bytes]"


def test_a_padded_payload_is_counted_in_decoded_bytes() -> None:
    # 1 and 2 bytes encode with "==" and "=": the count is of bytes, not of base64 characters.
    assert "1 bytes" in _content_to_text(_result(_image(base64.b64encode(b"x").decode())))
    assert "2 bytes" in _content_to_text(_result(_image(base64.b64encode(b"xy").decode())))


def test_an_embedded_text_resource_keeps_its_uri_and_its_text() -> None:
    resource = SimpleNamespace(uri="file:///notes/todo.md", mimeType="text/markdown",
                               text="- ship the fix", meta=None)
    out = _content_to_text(_result(SimpleNamespace(type="resource", resource=resource)))

    assert "file:///notes/todo.md" in out
    assert "- ship the fix" in out
    assert "resource=" not in out and "mimeType=" not in out


def test_an_embedded_blob_resource_is_a_placeholder_and_its_blob_never_arrives() -> None:
    resource = SimpleNamespace(uri="file:///shot.png", mimeType="image/png", blob=_PNG_B64)
    out = _content_to_text(_result(SimpleNamespace(type="resource", resource=resource)))

    assert _PNG_B64[:40] not in out
    assert "file:///shot.png" in out
    assert f"{len(_PNG_BYTES)} bytes" in out


def test_a_resource_link_is_named_by_its_uri() -> None:
    link = SimpleNamespace(type="resource_link", uri="https://example.test/report",
                           name="report", mimeType="text/html")
    out = _content_to_text(_result(link))

    assert "https://example.test/report" in out
    assert "type=" not in out


def test_a_block_of_a_type_we_do_not_know_is_named_not_dumped() -> None:
    odd = SimpleNamespace(type="hologram", payload="A" * 5_000)
    out = _content_to_text(_result(odd))

    assert "hologram" in out
    assert "AAAA" not in out


def test_structured_content_is_the_answer_when_no_text_block_came() -> None:
    out = _content_to_text(_result(structured={"temperature": 21.5, "unit": "C"}))

    assert '"temperature": 21.5' in out


def test_structured_content_is_not_repeated_when_a_text_block_carries_the_answer() -> None:
    # The spec asks servers that send structuredContent to ALSO send it serialised as text, for
    # clients like this one. Appending it again would hand the model the same answer twice.
    out = _content_to_text(_result(_text('{"temperature": 21.5}'), structured={"temperature": 21.5}))

    assert out == '{"temperature": 21.5}'


def test_structured_content_is_not_repeated_when_the_text_serialises_it_with_other_spacing() -> None:
    out = _content_to_text(_result(_text('{ "b": 2,\n  "a": 1 }'), structured={"a": 1, "b": 2}))

    assert out == '{ "b": 2,\n  "a": 1 }'


def test_structured_content_is_kept_when_the_text_is_only_a_summary_of_it() -> None:
    # Not every server follows the spec's SHOULD: here the text is for a human and the rows live
    # only in structuredContent. Dropping it because "some text came" hid the rows from the model.
    rows = {"rows": [{"id": 1}, {"id": 2}, {"id": 3}]}
    out = _content_to_text(_result(_text("Done, 3 rows"), structured=rows))

    assert out.startswith("Done, 3 rows\n")
    assert '"rows": [{"id": 1}, {"id": 2}, {"id": 3}]' in out


def test_an_empty_text_block_does_not_hide_structured_content() -> None:
    out = _content_to_text(_result(_text(""), structured={"ok": True}))

    assert '"ok": true' in out


def test_line_wrapped_base64_is_counted_without_its_newlines() -> None:
    wrapped = "\n".join(_PNG_B64[i : i + 76] for i in range(0, len(_PNG_B64), 76))

    out = _content_to_text(_result(_image(wrapped)))

    assert f"{len(_PNG_BYTES)} bytes" in out
    assert "\n" not in out


def test_structured_content_is_kept_beside_an_image_that_carries_no_text() -> None:
    out = _content_to_text(_result(_image(), structured={"width": 640}))

    assert "[image image/png" in out
    assert '"width": 640' in out


def test_plain_text_results_read_exactly_as_before() -> None:
    assert _content_to_text(_result(_text("a"), _text("b"))) == "a\nb"
    assert _content_to_text(_result()) == ""


class _FakeClientSession:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self._result


def _session_answering(result: Any) -> tuple[StdioMCPSession, asyncio.AbstractEventLoop]:
    """A session whose background loop runs and whose client returns ``result``; nothing spawned."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    session = StdioMCPSession("unused")
    session._loop = loop
    session._session = _FakeClientSession(result)
    return session, loop


def test_a_failure_with_an_image_is_still_an_error_and_still_carries_no_base64() -> None:
    session, loop = _session_answering(_result(_text("render failed"), _image(), is_error=True))
    try:
        out = session.call_tool("render", {})
    finally:
        loop.call_soon_threadsafe(loop.stop)

    assert out.startswith("error: render failed")
    assert _PNG_B64[:40] not in out


def test_a_successful_call_through_the_session_reaches_the_model_without_base64() -> None:
    session, loop = _session_answering(_result(_image()))
    try:
        out = session.call_tool("screenshot", {})
    finally:
        loop.call_soon_threadsafe(loop.stop)

    assert out == f"[image image/png, {len(_PNG_BYTES)} bytes]"
