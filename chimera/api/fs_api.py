"""Read-only filesystem helpers for the Code screen: a lazy one-level tree + a single file read.

Both are path-scoped by :func:`~chimera.tools.workspace.resolve_in_workspace` — the exact guard the
file tools use — so a ``..`` or absolute escape raises ``PathEscapesWorkspaceError`` (the endpoint
maps it to HTTP 400). Neither ever raises on a binary/dir/missing file: they degrade to an honest
note. The tree is lazy (immediate children only) so a huge repo doesn't serialize at once, and prunes
the same build/VCS dirs the checkpoint guard skips.

:func:`read_image` is the one path that hands back raw bytes, and it goes through the SAME
``resolve_in_workspace`` call as the text read — deliberately, because a second copy of a path guard
is a guard that ends up covering one of two callers. What it adds on top is a refusal: an extension
allowlist that yields ``image/*`` and nothing else. That refusal is the load-bearing part and the
reason this is an image reader rather than a byte reader — see :data:`_IMAGE_MEDIA_TYPES`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.checkpoint import _IGNORE_DIRS
from chimera.tools.workspace import atomic_write_text, read_text_for_edit, resolve_in_workspace

_MAX_READ_CHARS = 20_000  # mirrors ReadFileTool's cap
_MAX_WRITE_BYTES = 1_000_000  # 1 MB cap for the editable viewer's save
_MAX_IMAGE_BYTES = 20_000_000  # 20 MB cap for an inline preview, matching the attachment ceiling

#: The ONLY content types this module will ever put on a response body, keyed by extension.
#:
#: An allowlist, not :func:`mimetypes.guess_type`, because the failure mode is not a wrong icon. The
#: backend injects the bearer token into ``index.html`` as a ``<meta>`` tag for a loopback client, so
#: any DOCUMENT this origin serves can read that token back out of index.html with one same-origin
#: fetch and then drive the whole API as the user. ``guess_type`` answers ``text/html`` for a
#: ``.html`` file sitting in the workspace — exactly the file an attacker would plant there, via the
#: agent itself if a fetched page talked it into writing one.
#:
#: SVG is absent on purpose even though it *is* ``image/*``. An ``<img>`` will not run script inside
#: one, but a top-level navigation to the same URL will — in this origin, with the token one fetch
#: away. Nothing is lost by leaving it out: an SVG is UTF-8 text, so :func:`read_file` already
#: returns its source and the viewer highlights it. The case this reader exists for — the file the
#: viewer could not show at all — is the raster one.
#:
#: Kept separate from ``attachments.IMAGE_SUFFIXES`` even though the two lists nearly agree. That one
#: answers "will a vision model accept this?"; this one answers "may a browser render this in our
#: origin?". Sharing a constant would widen the second the next time somebody widens the first.
_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class UnsupportedImageError(Exception):
    """The path is not on the image allowlist, so its bytes are not served (the endpoint: 415)."""


class ImageTooLargeError(Exception):
    """The image is over the inline-preview byte cap (the endpoint: 413)."""


def list_tree(workspace: Path, rel: str, *, max_entries: int = 500) -> dict[str, Any]:
    """List the IMMEDIATE children of ``rel`` inside ``workspace`` (dirs first, then files, A→Z).

    Prunes ``_IGNORE_DIRS`` (``.git``, ``node_modules``, ``.chimera``, …), caps at ``max_entries``
    (flagging ``capped``), and returns each child's path relative to the workspace so the UI can
    expand/open it. A ``rel`` that is a file (not a dir) yields an empty list — never an error here.
    """
    root = Path(workspace).resolve()
    target = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    entries: list[dict[str, Any]] = []
    capped = False
    if target.is_dir():
        children = sorted(
            (p for p in target.iterdir() if p.name not in _IGNORE_DIRS),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
        for child in children:
            if len(entries) >= max_entries:
                capped = True
                break
            entries.append(
                {
                    "name": child.name,
                    "path": child.relative_to(root).as_posix(),
                    "is_dir": child.is_dir(),
                }
            )
    return {"workspace": str(root), "path": rel, "entries": entries, "capped": capped}


def read_file(workspace: Path, rel: str) -> dict[str, Any]:
    """Read ``rel`` as UTF-8 text (capped at ``_MAX_READ_CHARS``), mirroring ``ReadFileTool``.

    A directory, a binary/undecodable file, or a missing path returns an empty ``content`` + a short
    ``note`` — never a raise (except a path escape, which the caller turns into a 400).
    """
    root = Path(workspace).resolve()
    path = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    if path.is_dir():
        return {"path": rel, "content": "", "truncated": False, "note": "binary or non-text"}
    if not path.is_file():
        return {"path": rel, "content": "", "truncated": False, "note": "not found"}
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return {"path": rel, "content": "", "truncated": False, "note": "binary or non-text"}
    truncated = len(text) > _MAX_READ_CHARS
    if truncated:
        text = text[:_MAX_READ_CHARS]
    return {"path": rel, "content": text, "truncated": truncated, "note": ""}


def read_image(
    workspace: Path, rel: str, *, max_bytes: int = _MAX_IMAGE_BYTES
) -> tuple[bytes, str]:
    """Raw bytes of ``rel`` plus the ``image/*`` type it may be served as.

    Why it exists: ``render_chart`` and ``generate_image`` write PNGs into the workspace, and
    :func:`read_file` can only answer "binary or non-text" about them — our own app could not show
    the output of our own tools.

    Path-guarded by the SAME :func:`resolve_in_workspace` call the text read uses, so a ``..`` or
    absolute escape raises ``PathEscapesWorkspaceError`` exactly as it does there. Unlike the text
    read this one RAISES instead of degrading to a note, because there is no honest place to put a
    note in a response whose body is bytes — the caller turns each exception into a status.

    The media type comes from :data:`_IMAGE_MEDIA_TYPES` and from nowhere else. A ``.html`` in the
    workspace is REFUSED here rather than labelled: it would be served by us, same-origin, and this
    origin's page carries the bearer token in a ``<meta>`` tag. The suffix is read off the RESOLVED
    path so that the string which chose the type and the file which was opened cannot disagree.

    Raises ``PathEscapesWorkspaceError``, :class:`UnsupportedImageError`, ``FileNotFoundError``
    (missing path, or a directory) or :class:`ImageTooLargeError`.
    """
    root = Path(workspace).resolve()
    path = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    media_type = _IMAGE_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise UnsupportedImageError(f"{rel!r} is not a displayable image type")
    if not path.is_file():
        raise FileNotFoundError(rel)
    size = path.stat().st_size
    if size > max_bytes:
        # Checked before the read, not after: pulling a 2 GB file into memory in order to then
        # refuse it IS the denial of service, not the protection against one.
        raise ImageTooLargeError(f"{rel!r} is {size} bytes, over the {max_bytes}-byte preview cap")
    return path.read_bytes(), media_type


def write_file(
    workspace: Path, rel: str, content: str, *, max_bytes: int = _MAX_WRITE_BYTES
) -> dict[str, Any]:
    """Write ``content`` to ``rel`` inside ``workspace`` atomically, preserving an existing newline.

    Path-guarded by :func:`resolve_in_workspace` (a ``..``/absolute escape raises
    ``PathEscapesWorkspaceError``, which the endpoint maps to 400). The content is normalized to
    ``\\n`` first (a browser ``<textarea>`` already yields ``\\n``); if that UTF-8 body exceeds
    ``max_bytes`` a ``ValueError`` is raised (the endpoint maps it to 400) — the write never starts.

    Line endings are PRESERVED: an existing file's newline is detected via :func:`read_text_for_edit`
    and restored by :func:`atomic_write_text`, so saving a CRLF file keeps CRLF. A new file gets
    ``\\n`` and its parent directories are created. The write is atomic (temp + replace), so a failure
    mid-write can't truncate the user's file. Returns ``{path, bytes}`` (the bytes actually on disk,
    which may exceed the content length on a CRLF file).
    """
    root = Path(workspace).resolve()
    path = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    text = content.replace("\r\n", "\n")  # normalize to \n (the invariant read_text_for_edit expects)
    body = text.encode("utf-8")
    if len(body) > max_bytes:
        raise ValueError(f"content is {len(body)} bytes, over the {max_bytes}-byte limit")
    newline = "\n"
    if path.is_file():
        try:
            _, newline = read_text_for_edit(path)  # keep the file's own CRLF/LF convention
        except UnicodeDecodeError:
            newline = "\n"  # existing file isn't UTF-8 text; write plain \n
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text, newline=newline)
    return {"path": rel, "bytes": path.stat().st_size}


def browse_dirs(path: str, *, max_entries: int = 300) -> dict[str, Any]:
    """List sub-DIRECTORIES of ``path`` so a person can pick a project. Empty path = home.

    Deliberately narrower than :func:`list_tree`, which is scoped inside a chosen workspace and so
    cannot answer "which workspace?" at all. This one is not scoped — that is the point, and it is
    also the whole risk, so it is cut down to the least that answers the question:

    * **Directories only.** No files are listed and nothing is read. This enumerates folder NAMES;
      it is not a second way to read the disk.
    * **No hidden entries**, which keeps `.ssh` and friends out of a listing nobody asked to see.
    * **Capped**, and an unreadable directory returns an empty listing rather than raising: a
      permission error while browsing is an ordinary fact about somebody's home directory, not a
      failure of the app.

    The caller is still the localhost bind and the bearer guard; this adds no new door, only a
    smaller window in the one that exists.
    """
    root = Path(path).expanduser() if path else Path.home()
    try:
        root = root.resolve()
    except OSError:
        root = Path.home()
    entries: list[dict[str, str]] = []
    capped = False
    if root.is_dir():
        try:
            children = sorted(
                (c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")),
                key=lambda c: c.name.lower(),
            )
        except OSError:
            children = []
        if len(children) > max_entries:
            children, capped = children[:max_entries], True
        entries = [{"name": c.name, "path": str(c)} for c in children]
    parent = str(root.parent) if root.parent != root else ""
    return {"path": str(root), "parent": parent, "entries": entries, "capped": capped}


#: Names Windows refuses whatever the extension, and which fail in ways that do not look like a
#: bad name — a device, not a file. Checked on every platform: a folder made on Linux and synced
#: to Windows is somebody's Monday morning.
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def make_dir(parent: str, name: str) -> dict[str, Any]:
    """Create ONE folder inside ``parent`` so a project can be started without leaving the app.

    A write, where everything else on this browsing path is a read, so the name is treated as
    hostile even though the only caller is a text field on localhost:

    * **One segment.** Any separator, any drive letter, `.` or `..` — refused. The folder is
      created with ``mkdir``, never ``mkdir -p``, so even a name that slipped through could not
      build a tree.
    * **Checked after resolving too**, because the rules above are about strings and the filesystem
      has the last word: a name that resolves anywhere but a direct child of ``parent`` is refused.
    * **Reserved device names refused** on every platform, not only Windows.
    * **The parent must already exist.** This creates a folder, not a path.

    An existing folder is not an error — it is the answer to "make me this folder", already true.
    ``created`` says which happened so the screen can be honest about it.
    """
    limpo = name.strip().rstrip(". ")
    if not limpo or limpo in {".", ".."} or limpo.lower() in _RESERVED:
        raise ValueError("invalid folder name")
    if any(sep in limpo for sep in ("/", "\\", ":")) or limpo.startswith("."):
        raise ValueError("invalid folder name")
    base = Path(parent).expanduser()
    try:
        base = base.resolve()
    except OSError as exc:
        raise ValueError("invalid parent") from exc
    if not base.is_dir():
        raise ValueError("invalid parent")
    alvo = (base / limpo).resolve()
    if alvo.parent != base:
        raise ValueError("invalid folder name")
    if alvo.is_dir():
        return {"path": str(alvo), "created": False}
    try:
        alvo.mkdir()
    except OSError as exc:
        raise ValueError("could not create the folder") from exc
    return {"path": str(alvo), "created": True}
