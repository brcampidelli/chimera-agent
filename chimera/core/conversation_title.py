"""What a coding conversation is called in the list, from words someone wrote — never a model's.

The list used to call every conversation by the first 80 characters of its first message. That is a
good rule for a person typing a question and a useless one for an orchestrator: every task it hands
over opens with the same sentence ("Read `TAREFA.md` and do exactly what it asks…"), so twenty rows
read the same and none of them can be found again.

Four sources, in this order, and the first that has something wins:

1. the **owner's** rename (``CodeSessionMarks.title``) — the person said what it is called;
2. a title the **caller** passed with a turn (``CodeTurnRequest.title``) — an orchestrator naming the
   task it hands over;
3. the first ``# `` heading of the ONE Markdown file the first message points at inside the
   workspace — used only when the message is little besides that pointer (:data:`POINTER_WORDS`) and
   the file is not a hidden one. A message with a subject of its own is titled by that subject; a
   heading of a file the agent merely reads (or one of several named) never replaces what the person
   wrote (study 34, item 11);
4. the first message, as before.

None of them is generated. ``CodeSession.title`` says why: a title that costs a model call is
sometimes missing, and one that paraphrases can be wrong — and the owner decided (study 03) that
this stays true even as an option.

The heading is read from a file the MESSAGE names, so the message is untrusted and the file is
untrusted text: only a regular file inside the workspace, reached without passing through a link,
read up to a bound, and its heading stripped of control and formatting characters (a right-to-left
override in a title would reorder the row it sits in) and cut to a length.
"""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from pathlib import Path, PurePath
from typing import Literal

#: The longest title kept, after cleaning. Longer than the 80 a first message is cut to: a title
#: someone chose is worth more of the row than a sentence that happened to come first.
TITLE_MAX = 120

#: How much of a referenced Markdown file is read to find its heading. A task file's title is in
#: its first lines; reading further would only make a huge file a cost of starting a conversation.
HEADING_READ_BYTES = 16 * 1024

#: How many references in one message are tried. A message that names forty files is not naming
#: its task file, and each try is a few system calls.
MAX_REFERENCES = 5

#: A message that only points at a task file says little besides the pointer ("Read `TAREFA.md` and
#: do exactly what it asks"). One that says more than this many words besides the reference has a
#: subject of its own, and that subject — what the person wrote — is the title, not a file's heading.
POINTER_WORDS = 15

TitleSource = Literal["owner", "caller", "heading", "message"]

_BACKTICKED = re.compile(r"`([^`\n]{1,240}?\.md)`", re.IGNORECASE)
_BARE = re.compile(r"(?:^|(?<=[\s(\[\"']))([^\s`'\"()\[\]<>]{1,240}?\.md)(?=$|[\s)\]\"'.,;:!?])", re.IGNORECASE)
_HEADING = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^\s*(```|~~~)")

#: Windows' "this entry is a reparse point" bit: a junction is one and ``os.path.islink`` does not
#: see it on Python 3.11, so the walk below asks the attributes directly.
_REPARSE_POINT = 0x400


def clean_title(text: str, limit: int = TITLE_MAX) -> str:
    """``text`` as one plain line: no control or formatting characters, spaces collapsed, cut.

    Formatting characters go too (category ``Cf``): a right-to-left override or a zero-width joiner
    in a title is invisible in the row and reorders or hides what follows it.
    """
    spaced = "".join(
        " " if ch.isspace() else ch
        for ch in text
        if ch.isspace() or not unicodedata.category(ch).startswith("C")
    )
    return " ".join(spaced.split())[:limit].strip()


def referenced_markdown(message: str) -> list[str]:
    """The ``*.md`` paths a message names, backticked ones first, without repeats, at most
    :data:`MAX_REFERENCES`."""
    found: list[str] = []
    for pattern in (_BACKTICKED, _BARE):
        for match in pattern.finditer(message):
            ref = match.group(1).strip()
            if ref and ref not in found:
                found.append(ref)
    return found[:MAX_REFERENCES]


def _is_link(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return True  # cannot say what it is: treated as the thing to refuse
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _inside(root: Path, ref: str) -> Path | None:
    """The file ``ref`` names under ``root``, reached one component at a time with no link on the
    way, or None. ``..`` is refused rather than resolved: a reference that climbs is not a task file
    of this workspace, whatever it would resolve to."""
    raw = PurePath(ref.replace("\\", "/"))
    if raw.is_absolute() or raw.drive:
        try:
            rel = PurePath(os.path.normcase(str(raw))).relative_to(os.path.normcase(str(root)))
        except ValueError:
            return None
        parts = rel.parts
    else:
        parts = raw.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    current = root
    for part in parts:
        current = current / part
        if _is_link(current):
            return None
    try:
        if not stat.S_ISREG(current.lstat().st_mode):
            return None
        current.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return current


def first_heading(text: str) -> str:
    """The first level-one ATX heading (``# Title``) outside a fenced block, cleaned, or ""."""
    fenced = False
    for line in text.lstrip("﻿").splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _HEADING.match(line)
        if match:
            return clean_title(match.group(1))
    return ""


def _is_a_pointer(message: str, ref: str) -> bool:
    """The message is little besides the reference to ``ref``, and ``ref`` is not a hidden file
    (``.chimera-comum.md`` is a rules file shared by many tasks, never one task's own title)."""
    if PurePath(ref.replace("\\", "/")).name.startswith("."):
        return False
    return len(message.replace(ref, " ").split()) <= POINTER_WORDS


def heading_title(message: str, workspace: str | Path) -> str:
    """The heading of the first Markdown file ``message`` names that exists inside ``workspace``
    and has one; "" when none does — and the caller falls back to the first message."""
    if not str(workspace).strip():
        return ""
    root = Path(workspace)
    if not root.is_dir():
        return ""
    refs = referenced_markdown(message)
    if len(refs) != 1 or not _is_a_pointer(message, refs[0]):
        # Several files are named: which one is "the task" is a guess, and a wrong guess titled
        # conversations with the first heading of a shared-rules file (study 34, item 11).
        return ""
    for ref in refs:
        path = _inside(root, ref)
        if path is None:
            continue
        try:
            with path.open("rb") as handle:
                head = handle.read(HEADING_READ_BYTES)
        except OSError:
            continue
        title = first_heading(head.decode("utf-8", errors="replace"))
        if title:
            return title
    return ""


def choose_title(*, owner: str, stamped: str, stamped_source: str, derived: str) -> tuple[str, TitleSource]:
    """The title the list shows and where it came from: the owner's, then the stamped one (the
    caller's or the heading's), then the one derived from the first message."""
    if owner:
        return owner, "owner"
    if stamped and stamped_source in ("caller", "heading"):
        return stamped, "caller" if stamped_source == "caller" else "heading"
    return derived, "message"
