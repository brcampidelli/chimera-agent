"""In a conversation that runs on another machine, the file tools work on THAT machine, or refuse.

The SSH environment (:mod:`chimera.sandbox.ssh`) moves the agent's COMMANDS to a remote host, and the
files it would read or edit are over there too. The file tools are built for a folder of the machine
running Chimera; left in place they would be one wrong turn from editing the local checkout while the
agent believes it is changing the server. So in such a conversation each of them is swapped, by name
and with the same schema, for one of two things:

* **A remote form** (:data:`REMOTE_FILE_TOOLS`: ``read_file``, ``write_file``, ``edit_file``,
  ``apply_patch``, ``edit_batch``, ``list_dir``, ``grep``, ``glob``). Same name and same JSON schema as
  the local tool, so the approval policy (Manual, Accept edits, the "only reads" classifier) and the
  governance ledger treat it exactly as they treat the local one; same answers (line windows, truncation
  notices, error sentences), reusing the local tools' own helpers. What it does on the host, and how a
  path is kept inside the environment's folder, is :mod:`chimera.tools.ssh_remote`. Three things differ
  on purpose: ``read_file`` and ``grep`` mark their output untrusted (a file on another machine is
  outside input); a write to a file the host RUNS by itself (a shell rc file, a git hook, cron, systemd)
  asks the owner when the turn has read outside content; and the Manual card previews the change from
  the REMOTE file (:meth:`RemoteFileTool.remote_preview`), not from a local file of the same name.
* **A refusal** (the rest of :data:`LOCAL_FILE_TOOLS`): one sentence that says what to do instead.
  Tools that run a program or write a file on THIS machine (``execute_code``, ``render_chart``,
  ``open_pull_request``, ``design``, the media tools) have no remote meaning at all.

Both stay registered (a tool that vanishes reads as "I cannot do that" and invites improvising).
"""

from __future__ import annotations

import difflib
import posixpath
from pathlib import Path
from typing import Any

from chimera.sandbox.ssh import SshSandbox
from chimera.tools.base import Tool
from chimera.tools.edit import _parse_hunks, apply_hunks
from chimera.tools.files import lost_escapes, read_window, syntax_error
from chimera.tools.registry import ToolRegistry
from chimera.tools.search import _MAX_RESULTS
from chimera.tools.ssh_remote import (
    RemoteFileError,
    RemoteFiles,
    Resolved,
    clamp_limit,
    glob_regex,
    is_secret_name,
    refuse_nul,
    secret_in,
    startup_reason,
)
from chimera.tools.workspace import decode_for_edit, encode_after_edit

#: Every built-in tool that reads or writes a file of THIS machine, or runs a script written there.
LOCAL_FILE_TOOLS: frozenset[str] = frozenset({
    "read_file", "write_file", "edit_file", "apply_patch", "edit_batch", "list_dir", "grep", "glob",
    "read_document", "view_image", "download_media", "render_chart", "create_document",
    "generate_image", "text_to_speech", "generate_audio", "generate_video", "transcribe_audio",
    "open_pull_request", "execute_code",
    # Chimera Design: its `export` writes into the project folder of THIS machine.
    "design",
})

#: The tools that have a remote form below.
REMOTE_FILE_TOOLS: frozenset[str] = frozenset({
    "read_file", "write_file", "edit_file", "apply_patch", "edit_batch", "list_dir", "grep", "glob",
})


def refusal(host: str) -> str:
    return (
        f"error: this conversation runs on {host}; use run_shell to read or edit files there. "
        "This tool works on a folder of the machine running Chimera, not on the remote host, so it "
        "was not run."
    )


def is_remote(sandbox: object) -> bool:
    """Whether ``sandbox`` runs commands on another machine (an SSH environment)."""
    return isinstance(sandbox, SshSandbox)


class RefusedLocalFileTool(Tool):
    """A local file tool, kept by name and schema, that says why it does not run here."""

    def __init__(self, original: Tool, host: str) -> None:
        self.name = original.name
        self.parameters = original.parameters
        self.description = (
            f"UNAVAILABLE in this conversation, which runs on {host}: files are on that machine. "
            "Use run_shell there instead. " + original.description
        )
        self._host = host

    def run(self, **kwargs: Any) -> str:
        return refusal(self._host)


class RemoteFileTool(Tool):
    """A file tool that acts on the host of an SSH environment instead of on this machine.

    Built from the local tool it replaces: name and schema are copied, so nothing that keys on them (the
    approval policy, the read-only classifier, the ledger's write-tool list) sees a different tool.

    ``untrusted_output`` is NOT inherited from the local tool (which trusts the workspace by default):
    what a remote file holds was written by whoever has an account on that machine, or by anything that
    ever touched it, and the tools that return it (``read_file``, ``grep``) say so by setting it.
    """

    #: Appended to the description by a tool whose remote behaviour differs from the local one.
    description_note: str = ""
    #: What a tool returns comes from another machine: file content, file names. Every tool but the
    #: writers (whose receipt is our own sentence) marks it so.
    output_untrusted: bool = True

    def __init__(self, original: Tool, sandbox: SshSandbox) -> None:
        self.name = original.name
        self.parameters = original.parameters
        self.description = (
            f"{original.description} In this conversation it works on {sandbox.host} (over ssh), in "
            f"{sandbox.remote_path}: paths are relative to that folder and nothing outside it can be "
            "reached; credential files (.env, keys, .ssh, cloud and cluster configs) are refused."
            + self.description_note
        )
        self.untrusted_output = self.output_untrusted
        self.sandbox = sandbox
        self.files = RemoteFiles(sandbox)

    @property
    def remote_host(self) -> str:
        """The alias this tool works on: what the Manual card reads to know the write is not local."""
        return self.sandbox.host

    def run(self, **kwargs: Any) -> str:
        try:
            return self.act(**kwargs)
        except RemoteFileError as exc:
            return str(exc)
        except ValueError as exc:  # a NUL byte in an argument, a number that is not one
            return f"error: {exc}"

    def act(self, **kwargs: Any) -> str:
        raise NotImplementedError

    def remote_preview(self, args: dict[str, Any]) -> str:
        """The change this call would make, as a diff read from the REMOTE file (Manual's card).

        The card's own preview reads the file of the same relative path in the LOCAL workspace, which
        is a different file. Never raises: a preview that fails must not stop the question being asked.
        """
        return ""

    def existing_file(self, path: str) -> Resolved | None:
        """The resolved ``path`` when it is a file there, else None (the caller says "not found")."""
        target = self.files.resolve(path)
        return target if target.kind == "file" else None

    def guard_write(self, target: Resolved) -> str | None:
        """The refusal for a write to a file the host runs by itself (a shell rc file, a git hook or
        config, cron, systemd) in a turn that has read content from outside, else None.

        The same question a command asks (``bind_taint``): the owner's yes lets it through, no answer or
        no screen refuses. A turn that has read nothing from outside is not slowed down by it.
        """
        why = startup_reason(target.real, target.rel)
        if why is None:
            return None
        what = f"{self.name}: {target.real} on {self.sandbox.host} — {why}"
        if self.sandbox.confirm_when_tainted(what):
            return None
        return (
            f"error: {target.rel} is {why}. This turn has read content from outside, so changing it "
            f"needs the owner's approval, and it was not given (or nobody could be asked here). Do not retry."
        )

    def put(self, target: Resolved, data: bytes) -> None:
        """Write ``data`` to ``target`` on the host (atomic, and checked for completeness there)."""
        self.files.write_bytes(target, data)

    def _current_text(self, path: str) -> str:
        """The remote file's text for a preview; empty when it cannot be read."""
        try:
            target = self.existing_file(path)
            return "" if target is None else self.files.read_bytes(target).decode("utf-8", errors="replace")
        except Exception:  # a preview never raises
            return ""

    def _edit_preview(self, path: str, old: str, new: str, replace_all: bool) -> str:
        before = self._current_text(path)
        if before and old and old in before:
            after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
            return _unified(path, before, after, new_file=False)
        return "\n".join(
            [f"--- {path}", *(f"-{ln}" for ln in old.splitlines()), *(f"+{ln}" for ln in new.splitlines())]
        )


def _unified(rel: str, before: str, after: str, *, new_file: bool) -> str:
    return "\n".join(difflib.unified_diff(
        [] if new_file else before.splitlines(), after.splitlines(),
        fromfile="/dev/null" if new_file else f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=2,
    ))


class RemoteReadFile(RemoteFileTool):
    #: Windowed by `read_window`, as `read_file` is, and says how to continue (start_line).
    clips_own_output = True

    def act(self, **kwargs: Any) -> str:
        target = self.existing_file(str(kwargs["path"]))
        if target is None:
            return f"error: file not found: {kwargs['path']}"
        # Universal newlines, as the local tool's `read_text` gives them.
        text = self.files.read_bytes(target).decode("utf-8", errors="replace")
        text = text.replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10))
        return read_window(text, kwargs.get("start_line"), kwargs.get("max_lines"))


class RemoteWriteFile(RemoteFileTool):
    output_untrusted = False  # the receipt is our own sentence

    def act(self, **kwargs: Any) -> str:
        target = self.files.resolve(str(kwargs["path"]))
        content = str(kwargs.get("content", ""))
        name = posixpath.basename(target.real)
        # The same two refusals as the local tool, for the same reasons, run BEFORE the write.
        if not bool(kwargs.get("allow_invalid", False)):
            if why := syntax_error(Path(name), content):
                return (
                    f"error: refused to overwrite {name} — the content is not valid "
                    f"{Path(name).suffix.lstrip('.')}: {why}. Fix it, or pass allow_invalid=true if the "
                    f"file is meant to be invalid."
                )
            if why := lost_escapes(content):
                return (
                    f"error: refused to overwrite {name} — the text looks corrupted: {why}. "
                    f"Write the characters themselves (í, ã, ✕) rather than escape sequences. Pass "
                    f"allow_invalid=true if those sequences really are the intended content."
                )
        if (refused := self.guard_write(target)) is not None:
            return refused
        # The file's own line-ending convention survives an overwrite; a new file starts with LF.
        newline = "\n"
        if target.kind == "file":
            try:
                _, newline = decode_for_edit(self.files.read_bytes(target))
            except (UnicodeDecodeError, RemoteFileError):
                newline = "\n"  # not UTF-8, or too big to read back: the new text starts with LF
        self.put(target, encode_after_edit(content.replace("\r\n", "\n"), newline=newline))
        return f"wrote {len(content)} chars to {target.rel}"

    def remote_preview(self, args: dict[str, Any]) -> str:
        path = str(args.get("path", ""))
        before = self._current_text(path)
        return _unified(path, before, str(args.get("content", "")), new_file=not before)


class RemoteEditFile(RemoteFileTool):
    output_untrusted = False  # the receipt is our own sentence

    def act(self, **kwargs: Any) -> str:
        rel = kwargs["path"]
        target = self.existing_file(str(rel))
        old, new = str(kwargs["old"]), str(kwargs["new"])
        replace_all = bool(kwargs.get("replace_all", False))
        if target is None:
            return f"error: file not found: {rel}"
        if old == "":
            return "error: 'old' must be non-empty (it anchors the edit)"
        if old == new:
            return "error: 'old' and 'new' are identical — nothing to change"
        try:
            content, newline = decode_for_edit(self.files.read_bytes(target))
        except UnicodeDecodeError:
            return f"error: {rel} is not a UTF-8 text file — cannot edit"
        count = content.count(old)
        if count == 0:
            return f"error: 'old' not found in {rel} (must match exactly, including whitespace)"
        if count > 1 and not replace_all:
            return (
                f"error: 'old' appears {count} times in {rel} — add surrounding context to make "
                "it unique, or pass replace_all=true"
            )
        if (refused := self.guard_write(target)) is not None:
            return refused
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        self.put(target, encode_after_edit(updated, newline=newline))
        where = f"{count} occurrences" if replace_all else "1 occurrence"
        return f"edited {rel}: replaced {where}"

    def remote_preview(self, args: dict[str, Any]) -> str:
        return self._edit_preview(
            str(args.get("path", "")), str(args.get("old", "")), str(args.get("new", "")),
            bool(args.get("replace_all", False)),
        )


class RemoteApplyPatch(RemoteFileTool):
    output_untrusted = False  # the receipt is our own sentence

    def act(self, **kwargs: Any) -> str:
        rel = kwargs["path"]
        target = self.existing_file(str(rel))
        if target is None:
            return f"error: file not found: {rel}"
        try:
            hunks = _parse_hunks(str(kwargs["patch"]))
        except ValueError as exc:
            return f"error: {exc}"
        try:
            content, newline = decode_for_edit(self.files.read_bytes(target))
        except UnicodeDecodeError:
            return f"error: {rel} is not a UTF-8 text file — cannot edit"
        try:
            updated = apply_hunks(content, hunks)
        except ValueError as exc:
            return f"error: {exc}"
        if (refused := self.guard_write(target)) is not None:
            return refused
        self.put(target, encode_after_edit(updated, newline=newline))
        return f"applied {len(hunks)} hunk(s) to {rel}"

    def remote_preview(self, args: dict[str, Any]) -> str:
        return str(args.get("patch", ""))


class RemoteEditBatch(RemoteFileTool):
    """``edit_batch`` on the host: every edit is validated, path-guarded and taint-checked first, and
    nothing is written until all of them are. Each FILE then lands atomically; the batch as a whole is
    as atomic as the local one is (validation is all-or-nothing, a failing write reports what landed)."""

    output_untrusted = False  # the receipt is our own sentence

    def act(self, **kwargs: Any) -> str:
        edits = kwargs.get("edits")
        if not isinstance(edits, list) or not edits:
            return "error: 'edits' must be a non-empty array"
        planned: dict[str, tuple[Resolved, str, str]] = {}  # real path -> (target, new content, newline)
        for index, raw in enumerate(edits, start=1):
            if not isinstance(raw, dict):
                return f"error: edit {index} is not an object"
            rel = str(raw.get("path", ""))
            old, new = str(raw.get("old", "")), str(raw.get("new", ""))
            if not rel:
                return f"error: edit {index} has no 'path'"
            if old == "":
                return f"error: edit {index} has an empty 'old' (it anchors the edit)"
            if old == new:
                return f"error: edit {index} has identical 'old' and 'new' — nothing to change"
            try:
                expected = int(raw.get("count", 1))
            except (TypeError, ValueError, OverflowError):
                return f"error: edit {index} has a non-integer 'count'"
            if expected < 1:
                return f"error: edit {index} expects {expected} occurrences — must be >= 1"
            try:
                target = self.files.resolve(rel)
            except RemoteFileError as exc:
                return f"batch aborted, nothing written — edit {index} ({rel}): {exc}"
            if target.kind != "file":
                return f"batch aborted, nothing written — edit {index}: file not found: {rel}"
            if target.real in planned:
                _, content, newline = planned[target.real]
            else:
                if (refused := self.guard_write(target)) is not None:
                    return f"batch aborted, nothing written — edit {index} ({rel}): {refused}"
                try:
                    content, newline = decode_for_edit(self.files.read_bytes(target))
                except UnicodeDecodeError:
                    return f"batch aborted, nothing written — edit {index}: {rel} is not UTF-8 text"
            found = content.count(old)
            if found != expected:
                return (
                    f"batch aborted, NOTHING was written — edit {index} ({rel}): 'old' occurs "
                    f"{found} time(s), expected {expected}. Re-read the file and set 'count' to the "
                    "exact number."
                )
            planned[target.real] = (target, content.replace(old, new), newline)
        written: list[str] = []
        pending = list(planned.values())
        for position, (target, content, newline) in enumerate(pending):
            try:
                self.put(target, encode_after_edit(content, newline=newline))
            except Exception as exc:  # any failure here leaves a partial batch to report
                done = ", ".join(written) or "none"
                rest = ", ".join(t.rel for t, _, _ in pending[position + 1:]) or "none"
                return (
                    f"error: PARTIAL WRITE — {exc}. Already written: {done}. "
                    f"Failed on: {target.rel} (if the connection dropped or timed out during the move, "
                    f"it MAY OR MAY NOT have landed: read it before trusting either version). "
                    f"Not attempted: {rest}. The files are inconsistent."
                )
            written.append(target.rel)
        return f"edited {len(written)} file(s) in one batch: {', '.join(written)}"

    def remote_preview(self, args: dict[str, Any]) -> str:
        parts = []
        for edit in args.get("edits") or []:
            if isinstance(edit, dict):
                parts.append(self._edit_preview(
                    str(edit.get("path", "")), str(edit.get("old", "")), str(edit.get("new", "")), False
                ))
        return "\n".join(parts)


class RemoteListDir(RemoteFileTool):
    def act(self, **kwargs: Any) -> str:
        target = self.files.resolve(str(kwargs.get("path", ".")))
        if target.kind != "dir":
            return f"error: not a directory: {kwargs.get('path', '.')}"
        # A credential file is left out of a listing, not just refused when read: not even its name,
        # so a listing is not an invitation to try another spelling (the local tool does the same
        # for Chimera's own `.env`).
        entries = sorted(
            e for e in self.files.listing(target)
            if not is_secret_name(e.rstrip("/"))
            and secret_in("", posixpath.join(target.real, e.rstrip("/"))) is None
        )
        return "\n".join(entries) if entries else "(empty)"


class RemoteGrep(RemoteFileTool):
    description_note = (
        " The pattern is Perl-compatible when the host's grep supports -P, otherwise POSIX extended."
    )

    def act(self, **kwargs: Any) -> str:
        pattern, include = str(kwargs["pattern"]), str(kwargs.get("glob") or "")
        refuse_nul(pattern=pattern, glob=include)
        limit = clamp_limit(kwargs.get("max_results"), _MAX_RESULTS)
        target = self.files.resolve(str(kwargs.get("path", ".")))
        if target.kind == "none":
            return f"error: no such file or directory: {kwargs.get('path', '.')}"
        hits = self.files.grep(target, pattern, limit, include)
        lines: list[str] = []
        for path, number, text in hits:
            rel = posixpath.relpath(path, target.root)
            if rel.startswith("..") or secret_in(rel, path) is not None:
                continue  # the host was told to skip these; a name that slipped through is dropped here
            lines.append(f"{rel}:{number}: {text.strip()[:200]}")
        if len(hits) >= limit:
            return "\n".join(lines) + f"\n... [stopped at {limit} matches]"
        return "\n".join(lines) if lines else "no matches"


class RemoteGlob(RemoteFileTool):
    def act(self, **kwargs: Any) -> str:
        pattern = str(kwargs["pattern"])
        refuse_nul(pattern=pattern)
        limit = clamp_limit(kwargs.get("max_results"), _MAX_RESULTS)
        regex = glob_regex(pattern)
        target = self.files.resolve(".")
        paths, cut = self.files.files(target)
        matched = sorted(
            rel
            for p, rel in ((p, posixpath.relpath(p, target.root)) for p in paths)
            if not rel.startswith("..") and secret_in(rel, p) is None and regex.fullmatch(rel)
        )[:limit]
        note = "\n... [the host's file listing was cut; narrow the pattern]" if cut else ""
        return ("\n".join(matched) + note) if matched else "no files match" + note


_REMOTE_FORMS: dict[str, type[RemoteFileTool]] = {
    "read_file": RemoteReadFile,
    "write_file": RemoteWriteFile,
    "edit_file": RemoteEditFile,
    "apply_patch": RemoteApplyPatch,
    "edit_batch": RemoteEditBatch,
    "list_dir": RemoteListDir,
    "grep": RemoteGrep,
    "glob": RemoteGlob,
}


def block_local_file_tools(registry: ToolRegistry, host: str) -> list[str]:
    """Replace each local file tool in ``registry`` with a refusing one; return the names replaced.

    A tool that already has its remote form is left alone: this runs again for a tool registered after
    :func:`use_remote_files` (the Design tool), and must not undo the swap it made.
    """
    replaced: list[str] = []
    for name in sorted(LOCAL_FILE_TOOLS & set(registry.names())):
        current = registry.get(name)
        if isinstance(current, RemoteFileTool):
            continue
        registry.register(RefusedLocalFileTool(current, host), replace=True)
        replaced.append(name)
    return replaced


def use_remote_files(registry: ToolRegistry, sandbox: SshSandbox) -> list[str]:
    """Swap the file tools of ``registry`` for an SSH conversation: the ones in
    :data:`REMOTE_FILE_TOOLS` act on the host, every other local file tool refuses. Returns the names
    given a remote form."""
    ported: list[str] = []
    for name in sorted(REMOTE_FILE_TOOLS & set(registry.names())):
        registry.register(_REMOTE_FORMS[name](registry.get(name), sandbox), replace=True)
        ported.append(name)
    block_local_file_tools(registry, sandbox.host)
    return ported
