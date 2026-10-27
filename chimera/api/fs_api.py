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

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from chimera.core.checkpoint import _IGNORE_DIRS
from chimera.tools.svg_safety import MAX_SVG_BYTES, sanitise_svg
from chimera.tools.workspace import (
    PathEscapesWorkspaceError,
    atomic_write_text,
    read_text_for_edit,
    refuse_own_files,
    resolve_in_workspace,
)

_MAX_READ_CHARS = 20_000  # mirrors ReadFileTool's cap
#: How much the viewer may ask for when the person chooses to see a long file whole. The default stays
#: at the agent's cap; this is only reached by asking, and keeps a huge log from freezing the screen.
MAX_VIEW_CHARS = 1_000_000
_MAX_WRITE_BYTES = 1_000_000  # 1 MB cap for the editable viewer's save
_MAX_IMAGE_BYTES = 20_000_000  # 20 MB cap for an inline preview, matching the attachment ceiling
_MAX_MEDIA_BYTES = 50_000_000  # 50 MB: a narration or a short clip; the player holds it in memory. Streaming (Range) is a TODO

#: The ONLY content types this module will ever put on a response body, keyed by extension.
#:
#: An allowlist, not :func:`mimetypes.guess_type`, because the failure mode is not a wrong icon. The
#: backend injects the bearer token into ``index.html`` as a ``<meta>`` tag for a loopback client, so
#: any DOCUMENT this origin serves can read that token back out of index.html with one same-origin
#: fetch and then drive the whole API as the user. ``guess_type`` answers ``text/html`` for a
#: ``.html`` file sitting in the workspace — exactly the file an attacker would plant there, via the
#: agent itself if a fetched page talked it into writing one.
#:
#: SVG is served only as the output of :func:`chimera.tools.svg_safety.sanitise_svg`, never as the
#: stored bytes. A top-level navigation to an SVG runs its script in this origin, so a file that
#: fails validation is a 415 here, and a file that passes is served rebuilt from an allowlist with no
#: script, handlers, or outside references, under the SVG content-security policy the endpoint sets.
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
    ".svg": "image/svg+xml",
}

#: Audio and video the workspace may play inline, keyed by extension. The same rule as
#: :data:`_IMAGE_MEDIA_TYPES`: the type is chosen here and nowhere else, so an HTML file can never be
#: labelled as media and served from this origin.
_MEDIA_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


#: Documents the viewer shows as a TEXT preview (study 29, P6.3), keyed by suffix. ``create_document``
#: writes these and the viewer answered "binary or non-text" about every one, so "Open beside" on a
#: report would have opened a sentence saying it could not be shown. The preview is MarkItDown's text
#: of the file, returned in ``content`` like any text file, with ``document`` naming the format so the
#: viewer labels it a preview and never offers to edit it: saving that text would replace the .docx.
_DOCUMENT_FORMATS: dict[str, str] = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx"}
#: The same ceiling as an image preview. Checked before conversion, for the reason `read_image` gives.
_MAX_DOCUMENT_BYTES = 20_000_000


def _document_text(path: Path) -> str:
    """The document's text via MarkItDown (the `documents` extra). Raises ImportError without it."""
    from chimera.tools.documents import _markitdown_convert  # the read_document tool's own seam

    return _markitdown_convert(str(path))


def _read_document(
    path: Path, rel: str, fmt: str, max_chars: int = _MAX_READ_CHARS
) -> dict[str, Any]:
    """A text preview of a document, or an empty one with a note saying why there is none."""
    base: dict[str, Any] = {"path": rel, "content": "", "truncated": False, "document": fmt}
    if path.stat().st_size > _MAX_DOCUMENT_BYTES:
        return {**base, "note": "too large to preview"}
    try:
        text = _document_text(path)
    except ImportError:
        return {**base, "note": "previewing documents needs the 'documents' extra"}
    except Exception:  # noqa: BLE001 — a document that will not convert is a note, never a 500
        return {**base, "note": "could not be read as a document"}
    truncated = len(text) > max_chars
    return {
        **base, "content": text[:max_chars], "truncated": truncated, "note": "",
        "total_chars": len(text),
    }


class NotEditableTextError(Exception):
    """A document, or an existing file that is not UTF-8: a text save would destroy it (the endpoint: 400)."""


class UnsupportedImageError(Exception):
    """The path is not on the image allowlist, so its bytes are not served (the endpoint: 415)."""


class ImageTooLargeError(Exception):
    """The image is over the inline-preview byte cap (the endpoint: 413)."""


class UnsupportedMediaError(Exception):
    """The path is not on the audio/video allowlist, so its bytes are not served (the endpoint: 415)."""


def list_tree(workspace: Path, rel: str, *, max_entries: int = 500) -> dict[str, Any]:
    """List the IMMEDIATE children of ``rel`` inside ``workspace`` (dirs first, then files, A→Z).

    Prunes ``_IGNORE_DIRS`` (``.git``, ``node_modules``, ``.chimera``, …), caps at ``max_entries``
    (flagging ``capped``), and returns each child's path relative to the workspace so the UI can
    expand/open it. A ``rel`` that is a file (not a dir) yields an empty list — never an error here.
    """
    root = Path(workspace).resolve()
    target = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    refuse_own_files(target, "list")
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


def read_file(workspace: Path, rel: str, max_chars: int = _MAX_READ_CHARS) -> dict[str, Any]:
    """Read ``rel`` as UTF-8 text (capped at ``max_chars``), mirroring ``ReadFileTool`` by default.

    ``total_chars`` says how long the text is, so a viewer can offer the rest: it showed the first
    20,000 characters with a "truncated" badge and no way to read further (plan, 2026-10-10).

    A directory, a binary/undecodable file, or a missing path returns an empty ``content`` + a short
    ``note`` — never a raise (except a path escape, which the caller turns into a 400).
    """
    root = Path(workspace).resolve()
    path = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    refuse_own_files(path, "read")
    if path.is_dir():
        return {"path": rel, "content": "", "truncated": False, "note": "binary or non-text"}
    if not path.is_file():
        return {"path": rel, "content": "", "truncated": False, "note": "not found"}
    fmt = _DOCUMENT_FORMATS.get(path.suffix.lower())
    if fmt is not None:
        return _read_document(path, rel, fmt, max_chars)
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return {"path": rel, "content": "", "truncated": False, "note": "binary or non-text"}
    total = len(text)
    truncated = total > max_chars
    if truncated:
        text = text[:max_chars]
    return {
        "path": rel, "content": text, "truncated": truncated, "note": "", "total_chars": total,
    }


def refuse_rooted_path(rel: str) -> None:
    """Refuse a path that names a root of its own, before anything resolves it.

    ``workspace / rel`` throws the workspace away when ``rel`` is absolute, and ``Path.resolve`` then
    asks the operating system about the result. On Windows that reaches a network share, and the
    lookup sends the caller's credentials to the server the path names. So the answer is decided on
    the text alone: a leading slash or backslash (which also covers a UNC share and the
    extended-length prefix), a drive letter, or an absolute path of either flavour.
    """
    if rel.startswith(("\\", "/")):
        raise PathEscapesWorkspaceError(f"path {rel!r} names a root, not a file in the workspace")
    windows = PureWindowsPath(rel)
    if windows.drive or windows.anchor or windows.is_absolute() or PurePosixPath(rel).is_absolute():
        raise PathEscapesWorkspaceError(f"path {rel!r} names a drive or an absolute location")


def refuse_network_root(text: str) -> None:
    """Refuse a workspace given as a UNC share (``\\\\server\\share`` or ``//server/share``).

    The workspace parameter is resolved before anything else reads it, so a network path is turned
    away here, before ``resolve`` can reach the share.
    """
    if text.startswith(("\\\\", "//")):
        raise PathEscapesWorkspaceError(f"workspace {text!r} is a network share")


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
    refuse_rooted_path(rel)
    root = Path(workspace).resolve()
    path = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    refuse_own_files(path, "read")
    media_type = _IMAGE_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise UnsupportedImageError(f"{rel!r} is not a displayable image type")
    if not path.is_file():
        raise FileNotFoundError(rel)
    size = path.stat().st_size
    svg = media_type == "image/svg+xml"
    cap = min(max_bytes, MAX_SVG_BYTES) if svg else max_bytes
    if size > cap:
        # Checked before the read, not after: pulling a 2 GB file into memory in order to then
        # refuse it IS the denial of service, not the protection against one.
        raise ImageTooLargeError(f"{rel!r} is {size} bytes, over the {cap}-byte preview cap")
    data = path.read_bytes()
    if svg:
        # Re-validated on every read: a file written into the workspace by any other route is checked
        # exactly as a generated one, and what is served is the validator's output, not the file.
        try:
            data, _ = sanitise_svg(data)
        except ValueError as exc:
            raise UnsupportedImageError(f"{rel!r} is an SVG that failed validation") from exc
    return data, media_type


def read_media(
    workspace: Path, rel: str, *, max_bytes: int = _MAX_MEDIA_BYTES
) -> tuple[bytes, str]:
    """Raw bytes of ``rel`` plus the ``audio/*`` or ``video/*`` type it may be served as.

    The path guard and the refusals mirror :func:`read_image` line for line, for the same reason: one
    resolver, one place a type is chosen, and a size check before the read. A narration the agent
    wrote into the workspace is otherwise a file the viewer can only call "binary".

    Raises ``PathEscapesWorkspaceError``, :class:`UnsupportedMediaError`, ``FileNotFoundError`` or
    :class:`ImageTooLargeError` (the cap is the same error class; the endpoint words it for media).
    """
    refuse_rooted_path(rel)  # before any resolve: a UNC or drive path must not reach the share
    root = Path(workspace).resolve()
    path = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    refuse_own_files(path, "read")
    media_type = _MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise UnsupportedMediaError(f"{rel!r} is not a playable media type")
    if not path.is_file():
        raise FileNotFoundError(rel)
    # A second name for a file outside the workspace (a hard link) passes the resolve above, because it
    # is a plain file where it sits. Its link count says it has more than one name, so it is refused.
    if path.stat().st_nlink > 1:
        raise PathEscapesWorkspaceError(f"{rel!r} is hard-linked, so it may be a file from outside")
    # Read at most one byte past the cap. A `stat` size can be stale by the time the read happens, and
    # the read is what decides the size, so the cap is enforced on the bytes actually taken.
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageTooLargeError(f"{rel!r} is over the {max_bytes}-byte cap")
    return data, media_type


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
    refuse_own_files(path, "write")
    # A save is TEXT. `read_file` hands a .docx/.pdf/.xlsx/.pptx back as MarkItDown's text of it, so
    # a client that saves what it was shown would replace the document with its own preview; and an
    # existing non-UTF-8 file was shown as empty, so saving would replace it with whatever was typed.
    # Both are refused HERE rather than in one screen, so no client (the Edit tab, the Code viewer,
    # the bridge's files.write) can do either.
    if path.suffix.lower() in _DOCUMENT_FORMATS:
        raise NotEditableTextError(f"{rel!r} is a {path.suffix} document, not a text file")
    text = content.replace("\r\n", "\n")  # normalize to \n (the invariant read_text_for_edit expects)
    body = text.encode("utf-8")
    if len(body) > max_bytes:
        raise ValueError(f"content is {len(body)} bytes, over the {max_bytes}-byte limit")
    newline = "\n"
    if path.is_file():
        try:
            _, newline = read_text_for_edit(path)  # keep the file's own CRLF/LF convention
        except UnicodeDecodeError as exc:
            raise NotEditableTextError(f"{rel!r} is not a UTF-8 text file") from exc
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text, newline=newline)
    return {"path": rel, "bytes": path.stat().st_size}


#: How many entries :func:`find_paths` walks before it stops and says so. A repository past this is
#: answered from its first part, flagged ``capped``, rather than walked for seconds per keystroke.
MAX_PATHS_SCANNED = 20_000


def _path_score(query: str, path: str) -> float | None:
    """How well ``path`` answers ``query`` (higher is better), or None when it does not.

    A match in the last segment beats one elsewhere in the path, a prefix beats a substring, and a
    substring beats letters in order (``cvw`` finds ``ConversationWindow``). Shorter paths win ties:
    the file someone means is usually the one nearer the top.
    """
    if not query:
        return -float(path.count("/")) - len(path) / 1000
    q, p = query.lower(), path.lower()
    name = p.rsplit("/", 1)[-1]
    if name.startswith(q):
        return 3000 - len(p)
    if q in name:
        return 2000 - len(p)
    if q in p:
        return 1000 - len(p)
    score, at, last = 0.0, 0, -2
    for index, char in enumerate(p):
        if at < len(q) and char == q[at]:
            score += 5 if index == last + 1 else 1
            last, at = index, at + 1
    if at < len(q):
        return None
    return score - len(p) / 100


def find_paths(
    workspace: Path, query: str, *, limit: int = 50, max_scanned: int = MAX_PATHS_SCANNED
) -> dict[str, Any]:
    """Files and folders of ``workspace`` whose path matches ``query``, best first.

    The composer's ``@`` list. :func:`list_tree` answers one folder at a time and the text search
    answers "which lines contain this", so neither can answer "which file did I mean by ``login``".
    The walk prunes the folders the tree prunes (``_IGNORE_DIRS``) and never lists Chimera's own
    files, the rule every file route follows; it is a listing of names and reads nothing.
    """
    import os

    from chimera.core.own_files import protected_reason
    from chimera.tools.workspace import chimera_home

    root = Path(workspace).resolve()
    home = chimera_home()
    needle = query.strip().replace("\\", "/").lstrip("@")
    scored: list[tuple[float, str, bool]] = []
    scanned = 0
    capped = False
    for current, dirnames, filenames in os.walk(root):
        here = Path(current)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _IGNORE_DIRS and protected_reason(here / d, home) is None
        )
        entries = [(d, True) for d in dirnames] + [(f, False) for f in sorted(filenames)]
        for name, is_dir in entries:
            full = here / name
            if not is_dir and protected_reason(full, home) is not None:
                continue
            scanned += 1
            if scanned > max_scanned:
                capped = True
                break
            rel = full.relative_to(root).as_posix()
            score = _path_score(needle, rel)
            if score is not None:
                scored.append((score, rel, is_dir))
        if capped:
            break
    scored.sort(key=lambda item: (-item[0], item[1]))
    return {
        "entries": [{"path": rel, "is_dir": is_dir} for _, rel, is_dir in scored[:limit]],
        "capped": capped,
    }


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
