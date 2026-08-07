"""Files the user hands to the agent: images to look at, documents to read, speech to type.

Three things share this module because they share a decision. An attachment is content the user
CHOSE to send but did not WRITE — a screenshot from somewhere, a PDF someone emailed, a recording of
their own voice. Choosing to send something is not the same as vouching for it, so everything here
is treated the way fetched web pages are treated: extracted text is sanitized and data-fenced, and
the run that receives it is marked as having read untrusted content.

Where the files go matters too. They land in the app's own home, never in the user's workspace:
copying a file into someone's repository because they attached it to a message would be writing to
their project without being asked, and it would show up in their next `git status` as a mystery.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Extensions we send to a vision model as an image. Everything else is converted to text — which is
#: also what makes an unknown format degrade to "we tried to read it" rather than to a broken request.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

#: Text we can read ourselves. Everything else goes through the converter, which is an optional
#: dependency — so without this a plain `.txt` or a README reported "install the docs extra", which
#: is a wall in front of the simplest possible attachment.
TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java",
    ".c", ".h", ".cpp", ".sh", ".sql", ".xml", ".html", ".css",
})

#: A hard ceiling per file. Images are base64-encoded into the request, so a 40 MB photo is not a big
#: attachment, it is a failed turn and a bill for it.
MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Attachment:
    """One stored attachment. ``text`` is filled for documents, ``path`` for images."""

    id: str
    name: str
    kind: str  # "image" | "document"
    path: Path
    text: str = ""
    note: str = ""


def store_dir(home: Path) -> Path:
    return home / "attachments"


def save(home: Path, name: str, data: bytes) -> Attachment:
    """Store one uploaded file and, if it is a document, extract its text now.

    Extraction happens at upload rather than at turn time so the failure — an unreadable PDF, a
    missing optional dependency — surfaces while the user is still looking at the attach button,
    instead of halfway through a turn they are paying for.
    """
    if len(data) > MAX_BYTES:
        raise ValueError(f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")

    suffix = Path(name).suffix.lower()
    ident = uuid.uuid4().hex
    directory = store_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ident}{suffix}"
    path.write_bytes(data)

    if suffix in IMAGE_SUFFIXES:
        return Attachment(id=ident, name=name, kind="image", path=path)

    from chimera.governance.ledger_tool import fence
    from chimera.governance.sanitize import sanitize_untrusted
    from chimera.tools.documents import _MAX_CHARS, _markitdown_convert

    if suffix in TEXT_SUFFIXES:
        # Read directly. Sending a README through a document converter to get its own bytes back is
        # a dependency in front of the easiest thing anyone will attach — and the error it produced
        # ("install the docs extra") reads like the feature is broken rather than like the file is
        # exotic. Undecodable bytes fall through to the converter, which may know the encoding.
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        if text:
            if len(text) > _MAX_CHARS:
                text = text[:_MAX_CHARS] + f"\n... [truncated, {len(text)} chars total]"
            return Attachment(
                id=ident, name=name, kind="document", path=path,
                text=fence(sanitize_untrusted(text)),
            )

    try:
        text = _markitdown_convert(str(path))
    except ImportError:
        return Attachment(
            id=ident, name=name, kind="document", path=path,
            note="reading this format needs the `documents` extra — install it to attach this file type",
        )
    except Exception as exc:  # noqa: BLE001 — an unreadable file is a bad upload, not a crash
        return Attachment(id=ident, name=name, kind="document", path=path, note=f"could not read: {exc}")

    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n... [truncated, {len(text)} chars total]"
    # Same treatment the document TOOL gives a file it reads: a PDF can carry a prompt injection
    # exactly like a web page can, and the user picking the file does not make its contents theirs.
    return Attachment(
        id=ident, name=name, kind="document", path=path, text=fence(sanitize_untrusted(text))
    )


def load(home: Path, ident: str) -> Attachment | None:
    """Re-read a stored attachment by id. Returns None for an id we never issued."""
    if not ident or "/" in ident or "\\" in ident or "." in ident:
        return None  # ids are hex; anything else is a path trying to escape
    matches = sorted(store_dir(home).glob(f"{ident}*"))
    if not matches:
        return None
    path = matches[0]
    kind = "image" if path.suffix.lower() in IMAGE_SUFFIXES else "document"
    if kind == "image":
        return Attachment(id=ident, name=path.name, kind=kind, path=path)
    return Attachment(id=ident, name=path.name, kind=kind, path=path)


def transcribe(path: Path, language: str | None = None) -> str:
    """Speech to text, through the same tool the agent uses — local model if installed, else API.

    Deliberately the same path rather than a second implementation: a user dictating and an agent
    transcribing a recording should not be able to get different answers, or to have one work while
    the other is silently unconfigured.
    """
    from chimera.tools.media import TranscribeAudioTool

    tool = TranscribeAudioTool(path.parent)
    result: Any = tool.run(path=path.name, language=language)
    return str(result)


def vision_support(model: str) -> str:
    """``"yes"`` | ``"no"`` | ``"unknown"`` — can this model look at an image?

    Three states, and the third is the whole point. The answer comes from LiteLLM's model table, and
    a table has an edge: ``supports_vision`` returns False both for a model it knows cannot see AND
    for a model it has never heard of. Collapsing those two would tell someone running a brand-new
    vision model that it is blind — a confident wrong answer, which is worse than saying nothing,
    because they would go and disable a capability that works.

    So an unknown model reports ``unknown``, and the interface says "we do not know" rather than
    picking whichever guess reads better.
    """
    if not model:
        return "unknown"
    try:
        import litellm
    except ImportError:  # pragma: no cover — litellm is a hard dependency of the gateway
        return "unknown"
    try:
        info = litellm.get_model_info(model=model)
    except Exception:  # noqa: BLE001 — an unknown model raises, and unknown is a real answer here
        return "unknown"
    return "yes" if info.get("supports_vision") else "no"
