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
  the answer can link to it. The folder holds a ``.gitignore`` with ``*``, so the files do not get
  committed by accident;
* a run without a workspace, a turn that may not write (``read_only``), or a write region that
  refuses the folder, writes to the app's own ``mcp-output`` folder under the run's Chimera home. The
  placeholder says why.

Limits, all checked before anything is decoded or written:

* :data:`MAX_PAYLOAD_BYTES` (25 MB) for any one payload, estimated from the base64 length as
  ``len * 3 // 4``. Raster images are held to the 20 MB of :mod:`chimera.tools.image_safety`
  instead, the same limit ``generate_image`` applies. Nothing is silently passed over for being too
  large: an over-limit payload is refused with a placeholder that says so.
* :data:`RUN_BUDGET_BYTES` (100 MB) per run. The same payload seen twice is counted once.

Every stored file is recorded where the run's other writes are: on the taint ledger, which is what the
receipt reads, and on the audit trail, with its path, SHA-256 and size. The content came from a server
the run does not control, so the write is tainted.

Writes are atomic (a temporary file, then ``os.replace``), and refused when the folder or the file is a
link (a symlink or a junction), or when the resolved folder is outside the workspace. The link checks
narrow what a process can be tricked into writing; they are not a lock against a concurrent attacker.

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
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger
from chimera.tools.image_safety import MAX_IMAGE_BYTES, image_file
from chimera.tools.svg_safety import is_svg_document
from chimera.tools.write_region import WriteRegion, refuse_write

_log = get_logger("integrations.mcp_payloads")

#: Strings shorter than this are never payloads. A short base64 value is a token, a checksum or a
#: small label, and writing those out would turn ordinary answers into files.
MIN_PAYLOAD_CHARS = 4096

#: The most any one payload may be. Checked on the estimate ``len * 3 // 4`` before a base64 value is
#: decoded, so an oversized one is never decoded at all.
MAX_PAYLOAD_BYTES = 25 * 1024 * 1024

#: The most a single run may write through MCP output. A server that answers with a stream of
#: payloads cannot fill the disk one answer at a time.
RUN_BUDGET_BYTES = 100 * 1024 * 1024

#: The folder under the workspace, and the same name under the app's home, for MCP output.
OUTPUT_FOLDER = "mcp-output"

#: What the placeholder says when a run had no workspace to write into.
NO_PROJECT_NOTE = "no project folder in this run, so it is in the app folder"

#: What the placeholder says when the write region refused the project folder.
REFUSED_NOTE = "the write region does not allow the project's mcp-output folder, so it is in the app folder"

#: How deep into a JSON answer to look. A server's answer is data; nothing legitimate nests this far.
_MAX_DEPTH = 32

#: A MIME label is the server's string. The placeholder shows at most this much of it, on one line.
_LABEL_CHARS = 80

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

#: What gets told about each stored payload: (stored, sha256 prefix, size in bytes, the tool's name).
Recorder = Callable[["Stored", str, int, str], None]


class PayloadRefused(Exception):
    """Why a payload was not stored. The message is what the placeholder says."""


@dataclass(frozen=True)
class Stored:
    """A payload that reached disk: where it is, what the model is shown, and why it is there."""

    path: Path
    #: The path the model reads: relative to the workspace when the file is in the project.
    shown: str
    #: Empty when the file is where the run asked for it; otherwise why it is in the app folder.
    note: str = ""


class RunBudget:
    """How many bytes one run may write through MCP output, and which payloads it has counted.

    A payload is counted once per run, by its hash, so the same answer repeated (in the text and in
    ``structuredContent``, or in two calls) costs one budget entry.
    """

    def __init__(self, limit: int = RUN_BUDGET_BYTES) -> None:
        self.limit = limit
        self.used = 0
        self.lock = threading.Lock()
        self._counted: set[str] = set()

    def check(self, digest: str, size: int) -> None:
        if digest not in self._counted and self.used + size > self.limit:
            raise PayloadRefused("run budget")

    def commit(self, digest: str, size: int) -> None:
        if digest not in self._counted:
            self._counted.add(digest)
            self.used += size


@dataclass(frozen=True)
class PayloadSink:
    """Where payloads are written, and the gate every write passes before it touches disk.

    ``root`` is the folder payloads go into. ``workspace`` and ``write_region`` are the pair the
    file-writing tools check, so a region that refuses a path refuses this write too. A root inside
    the project's ``.chimera`` folder is refused by that gate on purpose: it holds the audit log and
    the run receipts, and a run must not write its own evidence.

    ``relative_to`` is the folder the placeholder's path is relative to (the workspace, for a project
    sink); None shows the absolute path. ``fallback`` is where the payload goes when this sink's
    write is refused. ``budget`` is the run's byte budget, shared by every copy of the sink made for
    that run. ``recorders`` are told about each stored file, and ``source`` names the tool that
    produced it.
    """

    root: Path
    workspace: Path
    write_region: WriteRegion | None = None
    relative_to: Path | None = None
    note: str = ""
    fallback: PayloadSink | None = None
    budget: RunBudget | None = None
    recorders: list[Recorder] = field(default_factory=list)
    source: str = "mcp"

    def store(self, data: bytes, extension: str) -> Stored:
        """Write ``data`` and say where it is. Raises :class:`PayloadRefused` when it is not stored."""
        digest = hashlib.sha256(data).hexdigest()[:32]
        if self.budget is None:
            stored = self._store(data, extension, digest)
        else:
            with self.budget.lock:
                self.budget.check(digest, len(data))
                stored = self._store(data, extension, digest)
                self.budget.commit(digest, len(data))
        for record in self.recorders:
            record(stored, digest, len(data), self.source)
        return stored

    def _store(self, data: bytes, extension: str, digest: str) -> Stored:
        path = self.root / f"{digest}{extension}"
        try:
            self._place(path, data)
        except PayloadRefused:
            if self.fallback is None:
                raise
            return replace(self.fallback._store(data, extension, digest), note=REFUSED_NOTE)
        return Stored(path=path, shown=self._shown(path), note=self.note)

    def _place(self, path: Path, data: bytes) -> None:
        if refusal := refuse_write(self.workspace, path, self.write_region):
            _log.warning("MCP payload not stored here: %s", refusal)
            raise PayloadRefused("write region")
        if _is_link(self.root) or _is_link(path):
            _log.warning("MCP payload not stored: the folder or the file is a link")
            raise PayloadRefused("link")
        if not path.parent.resolve().is_relative_to(self.workspace.resolve()):
            _log.warning("MCP payload not stored: the folder resolves outside the workspace")
            raise PayloadRefused("outside the workspace")
        if path.exists():
            return  # the same bytes, already here
        try:
            _ensure_folder(self.root)
            _write_atomic(path, data)
        except OSError as exc:
            # The model is told the payload was not saved; the turn goes on rather than failing here.
            _log.warning("MCP payload not stored (%s)", type(exc).__name__)
            raise PayloadRefused("could not write") from exc

    def _shown(self, path: Path) -> str:
        if self.relative_to is not None:
            with contextlib.suppress(ValueError):
                return path.resolve().relative_to(self.relative_to.resolve()).as_posix()
        return str(path)


def _is_link(path: Path) -> bool:
    """A symlink, or a junction on Windows. A junction a process did not make is a redirect too."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)  # Python 3.12+
    return bool(is_junction and is_junction())


def _ensure_folder(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".gitignore"
    if not marker.exists():
        _write_atomic(marker, b"*\n")


def _write_atomic(path: Path, data: bytes) -> None:
    """Write to a temporary file beside ``path``, then move it into place in one step."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "xb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()  # gone after a successful replace; left only by a failed write


def _app_sink(home: Path | None = None) -> PayloadSink:
    from chimera.config import get_settings

    root = Path(home if home is not None else get_settings().home) / OUTPUT_FOLDER
    return PayloadSink(root=root, workspace=root, note=NO_PROJECT_NOTE)


def default_sink() -> PayloadSink:
    """The app's own folder for MCP output, for a call that is not made inside a run.

    An MCP connection is shared by every conversation and every workspace, so it has no project
    folder of its own. The app's data folder is where the app keeps what it generates otherwise.
    """
    return _app_sink()


def sink_for_run(
    workspace: Path | None,
    write_region: WriteRegion | None,
    *,
    home: Path | None = None,
    budget_bytes: int = RUN_BUDGET_BYTES,
) -> PayloadSink:
    """The sink for one run: its workspace's ``mcp-output`` folder, under its write region.

    Without a workspace the payloads go to the app folder under ``home`` (the run's own Chimera home)
    and the placeholder says so. A workspace whose write region refuses ``mcp-output`` falls back to
    that app folder the same way. Each call makes a fresh byte budget, so each run has its own.
    """
    budget = RunBudget(budget_bytes)
    if workspace is None:
        return replace(_app_sink(home), budget=budget)
    root = Path(workspace).resolve()
    return PayloadSink(
        root=root / OUTPUT_FOLDER,
        workspace=root,
        write_region=write_region,
        relative_to=root,
        fallback=_app_sink(home),
        budget=budget,
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


def record_in_run(
    stored: Stored, digest: str, size: int, source: str, *, ledger: Any, audit: Any
) -> None:
    """Put one stored payload on the run's record: the taint ledger and the audit trail.

    The ledger keeps the event and ``untrusted_writes``, which the turn receipt lists.
    """
    ledger.record_untrusted_write(stored.shown, digest=digest, size=size, source=source)
    audit.record(
        "mcp.payload_written",
        {"path": stored.shown, "sha256": digest, "bytes": size, "source": source, "note": stored.note},
    )


def _label(mime: str) -> str:
    """A MIME label as a placeholder shows it: one line, bounded. The server chose the string."""
    return " ".join(mime.split())[:_LABEL_CHARS]


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


def _is_raster(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or data[:6] in (b"GIF87a", b"GIF89a")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def _prepare(data: bytes, mime: str) -> tuple[bytes, str | None]:
    """The bytes to store and their extension (None when the type is unknown).

    Raises :class:`PayloadRefused` for a payload that must not be stored: too large, or an SVG that
    does not sanitise. The image limit is checked here explicitly. The earlier code caught the size
    error from ``image_file`` as "not an image" and stored the raw bytes, which passed the limit.
    """
    if len(data) > MAX_PAYLOAD_BYTES:
        raise PayloadRefused("too large")
    if len(data) > MAX_IMAGE_BYTES and (mime.lower().startswith("image/") or _is_raster(data)):
        raise PayloadRefused("too large")
    try:
        return image_file(data)  # raster by magic bytes; an SVG comes back sanitised
    except ValueError:
        pass  # not a raster image: an SVG, or another type, decided below
    if is_svg_document(data):
        raise PayloadRefused("unsafe SVG")
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


def _refused(reason: str) -> str:
    return f"<not saved: {reason}>"


def _with_note(stored: Stored) -> str:
    return f"; {stored.note}" if stored.note else ""


def _string_payload(value: str, *, key: str, mime: str, sink: PayloadSink) -> str | None:
    """The placeholder for one long base64 string, or None when it is not a payload."""
    if len(value) < MIN_PAYLOAD_CHARS or _BASE64.fullmatch(value) is None:
        return None
    payload_key = _is_payload_key(key)
    if len(value) * 3 // 4 > MAX_PAYLOAD_BYTES:
        # Refused before any decoding. A long base64 string that is not named as a payload and has
        # no type is left alone, as every other long string is.
        return _refused("too large") if (payload_key or mime) else None
    raw = _decode_strict(value)
    if raw is None:
        return None
    try:
        clean, extension = _prepare(raw, mime)
        if extension is None:
            if not payload_key:
                return None
            extension = ".bin"
        stored = sink.store(clean, extension)
    except PayloadRefused as refusal:
        return _refused(str(refusal))
    label = _label(mime) or _EXTENSION_LABELS.get(extension, "application/octet-stream")
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
    label = _label(mime)
    if isinstance(data, (bytes, bytearray)):
        size = len(data)
    elif isinstance(data, str):
        size = len(data) * 3 // 4  # the estimate, before anything is decoded
    else:
        return f"[{kind} {label}, payload not decodable, not shown]"
    if size > MAX_PAYLOAD_BYTES:
        return f"[{kind} {label}] {_refused('too large')}"
    if isinstance(data, (bytes, bytearray)):
        raw: bytes | None = bytes(data)
    else:
        raw = _decode_strict("".join(data.split()))
    if raw is None:
        return f"[{kind} {label}, payload not decodable, not shown]"
    if not raw:
        return f"[{kind} {label}, 0 bytes]"
    try:
        clean, extension = _prepare(raw, mime)
        stored = sink.store(clean, extension or ".bin")
    except PayloadRefused as refusal:
        return f"[{kind} {label}, {len(raw)} bytes] {_refused(str(refusal))}"
    return f"[{kind} {label}, {len(clean)} bytes] <saved: {stored.shown}{_with_note(stored)}>"
