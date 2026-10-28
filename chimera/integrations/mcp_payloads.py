"""Binary payloads an MCP tool returns are written to a file, and the model reads a placeholder.

The case that made this necessary, measured on 2026-10-27 with Chimera 0.79.0: a VoiceStudio
``generate_speech`` answer carried a 230 KB WAV as base64, inside a JSON text block and again in
``structuredContent``. Both reached the model whole: 910 000 prompt tokens, US$ 0.11, a turn stopped
with ``context_stuck`` after a compaction, and the person never received the audio. Base64 is not
something a model can read, and the person asked for the file. So the bytes are written out, and the
model reads a short placeholder with the path, which it can cite or move.

Where the file goes depends on the run that made the call:

* a run with a workspace writes to ``<workspace>/mcp-output/``, a visible folder the desktop can
  serve, under the run's write region. The placeholder shows the path relative to the workspace, so
  the answer can link to it;
* a run without a workspace, or whose write region refuses that folder, writes to the app's own
  ``mcp-output`` folder under the Chimera home. The placeholder says why it is there.

Only two shapes are recognised:

* a content block of type ``image``, ``audio`` or ``resource`` that carries ``data`` or ``blob``;
* a string inside a JSON text block or inside ``structuredContent`` that is at least
  :data:`MIN_PAYLOAD_CHARS` long, is pure base64, and decodes. Its bytes must be a file type this
  module knows, unless its key names a payload (``*_base64``, ``*_b64``, ``*blob``, ``*_data``, or
  exactly ``data``): then an unknown type is stored as ``.bin``. Requiring a known type everywhere
  else is what keeps a long hex hash or identifier, which is also valid base64, out of the store.

Files are named by the SHA-256 of their bytes, so one payload that arrives in the text block and in
``structuredContent`` lands on one file.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import re
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger
from chimera.tools.image_safety import image_file
from chimera.tools.svg_safety import is_svg_document
from chimera.tools.write_region import WriteRegion, refuse_write

_log = get_logger("integrations.mcp_payloads")

#: Strings shorter than this are never payloads. A short base64 value is a token, a checksum or a
#: small label, and writing those out would turn ordinary answers into files.
MIN_PAYLOAD_CHARS = 4096

#: The project folder under the workspace, and the app folder under the Chimera home, for MCP output.
OUTPUT_FOLDER = "mcp-output"

#: What the placeholder says when a run had no workspace to write into.
NO_PROJECT_NOTE = "no project folder in this run, so it is in the app folder"

#: What the placeholder says when the write region refused the project folder.
REFUSED_NOTE = "the write region does not allow the project's mcp-output folder, so it is in the app folder"

#: How deep into a JSON answer to look. A server's answer is data; nothing legitimate nests this far.
_MAX_DEPTH = 32

_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_MIME_KEYS = ("mimeType", "mime_type", "mime", "content_type")

#: Extensions chosen from a label the server gave. Raster images are not listed: ``image_file``
#: identifies them by their magic bytes first. SVG is deliberately absent: it is only ever stored
#: after sanitising, so a label cannot make unsanitised markup a ``.svg`` on disk.
_MIME_EXTENSIONS: dict[str, str] = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "video/mp4": ".mp4",
    "application/pdf": ".pdf",
}

#: The MIME type a placeholder names when the server gave none, from the extension we chose.
_EXTENSION_LABELS: dict[str, str] = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


@dataclass(frozen=True)
class Stored:
    """A payload that reached disk: where it is, what the model is shown, and why it is there."""

    path: Path
    #: The path the model reads: relative to the workspace when the file is in the project.
    shown: str
    #: Empty when the file is where the run asked for it; otherwise why it is in the app folder.
    note: str = ""


@dataclass(frozen=True)
class PayloadSink:
    """Where payloads are written, and the gate every write passes before it touches disk.

    ``root`` is the folder payloads go into. ``workspace`` and ``write_region`` are the pair the
    file-writing tools check, so a region that refuses a path refuses this write too. A root inside
    the project's ``.chimera`` folder is refused by that gate on purpose: it holds the audit log and
    the run receipts, and a run must not write its own evidence.

    ``relative_to`` is the folder the placeholder's path is relative to (the workspace, for a project
    sink); None shows the absolute path. ``fallback`` is where the payload goes when this sink's
    write is refused, and ``note`` is said in the placeholder of a payload this sink stores.
    """

    root: Path
    workspace: Path
    write_region: WriteRegion | None = None
    relative_to: Path | None = None
    note: str = ""
    fallback: PayloadSink | None = None

    def store(self, data: bytes, extension: str) -> Stored | None:
        """Write ``data`` under ``root`` and say where it is, or None when it was not stored."""
        digest = hashlib.sha256(data).hexdigest()[:32]
        path = self.root / f"{digest}{extension}"
        if self._write(path, data):
            return Stored(path=path, shown=self._shown(path), note=self.note)
        if self.fallback is None:
            return None
        found = self.fallback.store(data, extension)
        return None if found is None else replace(found, note=REFUSED_NOTE)

    def _write(self, path: Path, data: bytes) -> bool:
        if refusal := refuse_write(self.workspace, path, self.write_region):
            _log.warning("MCP payload not stored here: %s", refusal)
            return False
        try:
            if not path.exists():
                self.root.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        except OSError as exc:
            # The model is told the payload was not saved; the turn goes on rather than failing here.
            _log.warning("MCP payload not stored (%s)", type(exc).__name__)
            return False
        return True

    def _shown(self, path: Path) -> str:
        if self.relative_to is not None:
            with contextlib.suppress(ValueError):
                return path.resolve().relative_to(self.relative_to.resolve()).as_posix()
        return str(path)


def _app_sink(home: Path | None = None) -> PayloadSink:
    from chimera.config import get_settings

    root = Path(home if home is not None else get_settings().home) / OUTPUT_FOLDER
    return PayloadSink(root=root, workspace=root, note=NO_PROJECT_NOTE)


def default_sink() -> PayloadSink:
    """The app's own folder for MCP output, for a call that is not made inside a run with a workspace.

    An MCP connection is shared by every conversation and every workspace, so it has no project
    folder of its own. The app's data folder is where the app keeps what it generates otherwise.
    """
    return _app_sink()


def sink_for_run(
    workspace: Path | None, write_region: WriteRegion | None, *, home: Path | None = None
) -> PayloadSink:
    """The sink for one run: its workspace's ``mcp-output`` folder, under its write region.

    Without a workspace the payloads go to the app folder under ``home`` (the run's own Chimera home)
    and the placeholder says so. A workspace whose write region refuses ``mcp-output`` falls back to
    that app folder the same way.
    """
    if workspace is None:
        return _app_sink(home)
    root = Path(workspace).resolve()
    return PayloadSink(
        root=root / OUTPUT_FOLDER,
        workspace=root,
        write_region=write_region,
        relative_to=root,
        fallback=_app_sink(home),
    )


#: The sink of the MCP call in progress, bound by the tool that makes it. A connection is shared, so
#: the sink cannot be fixed on the session; it has to follow the call. The call's result is turned
#: into text in the thread that made the call, which is where this is read.
_CALL_SINK: ContextVar[PayloadSink | None] = ContextVar("mcp_call_payload_sink", default=None)


@contextlib.contextmanager
def bound_sink(sink: PayloadSink | None) -> Iterator[None]:
    """Route the payloads of MCP calls made inside this block to ``sink``."""
    token = _CALL_SINK.set(sink)
    try:
        yield
    finally:
        _CALL_SINK.reset(token)


def call_sink() -> PayloadSink | None:
    """The sink bound for the MCP call in progress, or None."""
    return _CALL_SINK.get()


def _media_extension(data: bytes) -> str | None:
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav"
    if data.startswith(b"OggS"):
        return ".ogg"
    if data.startswith(b"fLaC"):
        return ".flac"
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if data[4:8] == b"ftyp":
        return ".mp4"
    return None


def _prepare(data: bytes, mime: str) -> tuple[bytes, str | None] | None:
    """The bytes to store and their extension (None when the type is unknown), or None to refuse.

    Raster images and SVG go through ``image_file``, the same check ``generate_image`` applies, so a
    stored SVG is the sanitised one. A document that looks like SVG but does not sanitise is refused.
    """
    try:
        return image_file(data)
    except ValueError:
        pass
    if is_svg_document(data):
        return None
    return data, _media_extension(data) or _MIME_EXTENSIONS.get(mime.lower())


def _decode_strict(text: str) -> bytes | None:
    if not _BASE64.fullmatch(text):
        return None
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


def _is_payload_key(key: str) -> bool:
    lowered = key.lower()
    return lowered == "data" or lowered.endswith(("base64", "b64", "blob", "_data"))


def _with_note(stored: Stored) -> str:
    return f"; {stored.note}" if stored.note else ""


def _string_payload(value: str, *, key: str, mime: str, sink: PayloadSink) -> str | None:
    """The placeholder for one long base64 string, or None when it is not a payload."""
    if len(value) < MIN_PAYLOAD_CHARS:
        return None
    raw = _decode_strict(value)
    if raw is None:
        return None
    prepared = _prepare(raw, mime)
    if prepared is None:
        return f"<not saved: unsafe SVG, {len(raw)} bytes>"
    clean, extension = prepared
    if extension is None:
        if not _is_payload_key(key):
            return None
        extension = ".bin"
    label = mime or _EXTENSION_LABELS.get(extension, "application/octet-stream")
    stored = sink.store(clean, extension)
    if stored is None:
        return f"<not saved: {len(clean)} bytes, {label}>"
    return f"<saved: {stored.shown}, {len(clean)} bytes, {label}{_with_note(stored)}>"


def rewrite_payloads(value: Any, sink: PayloadSink, _depth: int = 0) -> Any:
    """``value`` with every payload string replaced by its placeholder. Nothing else is touched."""
    if _depth > _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        mime = ""
        for key in _MIME_KEYS:
            if isinstance(value.get(key), str):
                mime = str(value[key])
                break
        rewritten: dict[Any, Any] = {}
        for key, item in value.items():
            replaced = None
            if isinstance(item, str) and isinstance(key, str):
                replaced = _string_payload(item, key=key, mime=mime, sink=sink)
            rewritten[key] = (
                replaced if replaced is not None else rewrite_payloads(item, sink, _depth + 1)
            )
        return rewritten
    if isinstance(value, list):
        return [rewrite_payloads(item, sink, _depth + 1) for item in value]
    return value


def rewrite_text(text: str, sink: PayloadSink) -> str:
    """A text block whose JSON carries a payload, with the payload replaced.

    Anything else comes back unchanged, spacing included, so a plain answer reads exactly as before.
    """
    if len(text) < MIN_PAYLOAD_CHARS or text.lstrip()[:1] not in ("{", "["):
        return text
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    rewritten = rewrite_payloads(parsed, sink)
    if rewritten == parsed:
        return text
    return json.dumps(rewritten, ensure_ascii=False)


def binary_block_text(kind: str, mime: str, data: Any, sink: PayloadSink) -> str:
    """The text the model reads for an ``image``, ``audio`` or ``resource`` block's payload."""
    raw: bytes | None
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    elif isinstance(data, str):
        raw = _decode_strict("".join(data.split()))
    else:
        raw = None
    if raw is None:
        return f"[{kind} {mime}, payload not decodable, not shown]"
    if not raw:
        return f"[{kind} {mime}, 0 bytes]"
    prepared = _prepare(raw, mime)
    if prepared is None:
        return f"[{kind} {mime}, {len(raw)} bytes] <not saved: unsafe SVG>"
    clean, extension = prepared
    stored = sink.store(clean, extension or ".bin")
    if stored is None:
        return f"[{kind} {mime}, {len(clean)} bytes] <not saved>"
    return f"[{kind} {mime}, {len(clean)} bytes] <saved: {stored.shown}{_with_note(stored)}>"
