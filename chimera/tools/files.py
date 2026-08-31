"""Filesystem tools: read, write and list within a workspace root."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.workspace import atomic_write_text, resolve_in_workspace
from chimera.tools.write_region import WriteRegion, refuse_write

_MAX_READ_CHARS = 20_000


class _WorkspaceTool(Tool):
    """Base for tools bound to a workspace root (with an optional declared write-region)."""

    def __init__(
        self, workspace: Path | None = None, *, write_region: WriteRegion | None = None
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.write_region = write_region


class ReadFileTool(_WorkspaceTool):
    name = "read_file"
    description = "Read a UTF-8 text file from the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."}
        },
        "required": ["path"],
    }

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        write_region: WriteRegion | None = None,
        trust_workspace: bool = True,
    ) -> None:
        super().__init__(workspace, write_region=write_region)
        # When the workspace is NOT trusted (running on third-party code), a file's contents are
        # untrusted external input — a poisoned source/README can carry a prompt injection. Marking
        # the output untrusted routes it through the taint ledger + fence (ledger_tool.py reads this
        # attribute), so the run taints and the dangerous-tool gate arms, just like a fetched page.
        # Default trusts the workspace (your own repo) so `--taint` isn't tripped by every file read.
        self.untrusted_output = not trust_workspace

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        if not path.is_file():
            return f"error: file not found: {kwargs['path']}"
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > _MAX_READ_CHARS:
            return text[:_MAX_READ_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        return text


def syntax_error(path: Path, content: str) -> str | None:
    """Why ``content`` cannot be the whole of ``path``, or None if it can.

    Only formats we can check for free with the standard library, and only on a FULL overwrite.
    That scope is the whole idea: a surgical edit may legitimately pass through a state that does
    not parse — a half-applied rename, a function being moved — while a complete file that does not
    parse is never what anyone meant. Checking both would turn a normal editing rhythm into a fight.
    """
    suffix = path.suffix.lower()
    if suffix == ".py":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            where = f" (line {exc.lineno}" + (f", col {exc.offset}" if exc.offset else "") + ")"
            return f"{exc.msg}{where}"
        except ValueError as exc:  # e.g. source with NUL bytes
            return str(exc)
    elif suffix == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return f"{exc.msg} (line {exc.lineno}, col {exc.colno})"
    return None


#: `u` and four hex digits with NO backslash in front — a `\uXXXX` that lost its backslash.
#: No word boundary: the real defect arrives welded into the prose ("Utensu00edlios").
_LOOSE_ESCAPE = re.compile(r"(?<!\\)u([0-9a-fA-F]{4})")
#: The ES6 `\u{1F50D}` form, same loss.
_LOOSE_ESCAPE_BRACED = re.compile(r"(?<!\\)u\{(1?[0-9A-Fa-f]{4,5})\}")

#: Below this it is a coincidence, not a pattern. The real case had 23.
_LOOSE_ESCAPE_MIN = 3

#: Code points a lost backslash plausibly produces in Latin-script text: accents, typographic
#: punctuation, currency, arrows and dingbats, emoji.
#:
#: Narrow on purpose, and the narrowing was measured rather than reasoned. A first version accepted
#: any code point of a textual Unicode category and fired on **79 of 32,655 files in this
#: repository** — because `succeeded` contains `uccee`, `c`, `c`, `e` and `e` are all hex digits,
#: and U+CCEE is a CJK ideograph. An ideograph never reaches a file through a lost escape; an `i`
#: with an acute accent does it constantly. With these ranges: 0 of 32,655, and the real defect
#: still caught.
_LOOSE_ESCAPE_RANGES = (
    (0x00A0, 0x024F),  # Latin-1 Supplement + Latin Extended-A/B — the accents
    (0x2010, 0x206F),  # typographic punctuation — dashes, curly quotes, ellipsis
    (0x20A0, 0x20BF),  # currency
    (0x2190, 0x27BF),  # arrows, maths, dingbats
    (0x2B00, 0x2BFF),  # more arrows and symbols
    (0x1F300, 0x1FAFF),  # emoji
)


def lost_escapes(content: str) -> str | None:
    r"""Why ``content`` looks like text whose escape sequences lost their backslashes, or None.

    A model writing a file full of accented prose emits `\u00ed` for `í`. When the backslash does
    not survive — a re-serialisation, a shell hop, a provider quirk — what lands is `u00ed`, welded
    into the word. Nothing downstream complains: the file is valid UTF-8, valid HTML, and the page
    renders `Utensu00edlios` to a human being.

    Measured on a real run: an agent wrote four files in one go; three carried their accents
    correctly and the fourth had **zero** accented characters and 23 orphan sequences. The verify
    command passed, the diff gate accepted, and the corruption reached the user's screen.

    Two conditions together, because either alone misfires. **Three or more** matches, since one
    hex-looking fragment is a coincidence. And **fewer real non-ASCII characters than orphan
    sequences** — the signal is not that escapes appear, it is that they appear *instead of* the
    characters they encode. A correctly written Portuguese file has hundreds of real accents and
    would never trip this; the corrupted one had none.
    """
    found: list[str] = []
    for pattern in (_LOOSE_ESCAPE, _LOOSE_ESCAPE_BRACED):
        for match in pattern.finditer(content):
            code = int(match.group(1), 16)
            if any(low <= code <= high for low, high in _LOOSE_ESCAPE_RANGES):
                found.append(match.group(0))
    if len(found) < _LOOSE_ESCAPE_MIN:
        return None
    real = sum(1 for ch in content if ord(ch) > 0x7F)
    if real >= len(found):
        return None
    sample = ", ".join(list(dict.fromkeys(found))[:6])
    return (
        rf"{len(found)} sequences read as `\uXXXX` with the backslash missing ({sample}), "
        f"and the file has only {real} real non-ASCII characters"
    )


class WriteFileTool(_WorkspaceTool):
    name = "write_file"
    description = "Write (create or overwrite) a UTF-8 text file in the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "content": {"type": "string", "description": "Full file content to write."},
            "allow_invalid": {
                "type": "boolean",
                "description": (
                    "Write even if the content does not parse (.py/.json). Use for templates, "
                    "fixtures and partial files that are invalid on purpose."
                ),
            },
        },
        "required": ["path", "content"],
    }

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs["path"]))
        if err := refuse_write(self.workspace, path, self.write_region):
            return err
        content = str(kwargs.get("content", ""))
        # Refused BEFORE the write, not reported after. A file that does not parse is one the next
        # step cannot read, import or test, so writing it costs a whole verify cycle to discover
        # something `ast.parse` knew for free — and on a full overwrite it also destroys the version
        # that did parse.
        #
        # The escape hatch is not optional. Templates, fixtures and deliberately-broken examples are
        # real files, and a guard with no way past turns "the agent wrote something odd" into "the
        # agent cannot do this at all".
        if not bool(kwargs.get("allow_invalid", False)):
            if why := syntax_error(path, content):
                return (
                    f"error: refused to overwrite {path.name} — the content is not valid "
                    f"{path.suffix.lstrip('.')}: {why}. Fix it, or pass allow_invalid=true if the "
                    f"file is meant to be invalid."
                )
            # Its own message, deliberately, and not folded into the one above: this content may
            # parse perfectly. Reporting it as "not valid html" would name the wrong problem and
            # send the next attempt looking for a syntax error that is not there.
            if why := lost_escapes(content):
                return (
                    f"error: refused to overwrite {path.name} — the text looks corrupted: {why}. "
                    f"Write the characters themselves (í, ã, ✕) rather than escape sequences. Pass "
                    f"allow_invalid=true if those sequences really are the intended content."
                )
        path.parent.mkdir(parents=True, exist_ok=True)
        # Byte-exact atomic write: never OS-translate the model's newlines, and never truncate an
        # existing file if the write is interrupted (temp + replace).
        atomic_write_text(path, content)
        return f"wrote {len(content)} chars to {path.relative_to(self.workspace)}"


class ListDirTool(_WorkspaceTool):
    name = "list_dir"
    description = "List entries of a directory in the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path relative to the workspace (default '.').",
            }
        },
    }

    def run(self, **kwargs: Any) -> str:
        path = resolve_in_workspace(self.workspace, str(kwargs.get("path", ".")))
        if not path.is_dir():
            return f"error: not a directory: {kwargs.get('path', '.')}"
        entries = sorted(f"{p.name}/" if p.is_dir() else p.name for p in path.iterdir())
        return "\n".join(entries) if entries else "(empty)"
