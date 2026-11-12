"""Manual mode: every file edit and every command asks on the turn's own card, before it happens.

Manual is the permission mode that stops before each edit and each command. The question is the one
a command already used (``run_shell: <command>`` on the approval card, ``code_api._ask_command_on_card``);
this module adds the edit half and the one thing the card needed besides: a yes that lasts for the
conversation, for exactly what was asked.

Three decisions worth stating.

**It only narrows.** The gate sits INSIDE the trust kernel and the taint ledger, so a call they
refuse never reaches a question, and a denial the posture, the denylist or governance state is never
softened by a yes here. A yes releases the call to the tool, which still applies the write region and
the workspace jail.

**A turn with no screen refuses at once.** A bot, a cron job, a background work and a bridge that
cannot approve have nobody to answer; a question written for them is a timeout with a bill, which is
the failure this whole approval design exists to avoid. They get a plain sentence instead.

**"Allow in this conversation" is bounded.** An edit is allowed per TOOL (the whole of ``edit_file``
for this conversation); a command is allowed per EXACT COMMAND, never for the shell as a whole. The
allowance lives in memory, belongs to the conversation, and is dropped when the conversation leaves
Manual, so a restart or a mode change always goes back to asking.
"""

from __future__ import annotations

import difflib
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from chimera.governance.ledger import WORKSPACE_WRITERS, WriterSpec
from chimera.tools.base import Tool, refusal

#: The name MCP payload writes ask under (the sink writes in the workspace but is not a tool).
MCP_PAYLOAD = "mcp_payload"

#: The longest command (or key) "Allow in this conversation" is offered for. Past it only "once":
#: the key is stored and shown, and a very long one is text nobody read.
MAX_KEY_CHARS = 2000

#: What a turn nobody is watching is told. A sentence the model can relay, never a hang.
NEEDS_SCREEN = (
    "Manual mode asks the owner before each edit and each command, and this turn has no screen to "
    "show the question on. Nothing was run. Switch the conversation to a mode that does not ask, "
    "or run this from the Code screen."
)

#: What a declined edit tells the model.
EDIT_DECLINED = "the owner declined this edit in Manual mode. Nothing was written."

#: Longest diff shown on a card. The cut is said, never silent.
MAX_DIFF_CHARS = 4000
_MAX_READ_BYTES = 1_000_000


class ManualAllowances:
    """What "Allow in this conversation" granted, per conversation, in memory only."""

    def __init__(self) -> None:
        self._by_session: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def allow(self, session_id: str, key: str) -> bool:
        """Grant ``key``; True when it was not already granted (what :meth:`revoke` may undo)."""
        if not session_id or not key:
            return False
        with self._lock:
            keys = self._by_session.setdefault(session_id, set())
            fresh = key not in keys
            keys.add(key)
            return fresh

    def revoke(self, session_id: str, key: str) -> None:
        with self._lock:
            self._by_session.get(session_id, set()).discard(key)

    def allowed(self, session_id: str, key: str) -> bool:
        with self._lock:
            return bool(session_id) and key in self._by_session.get(session_id, set())

    def clear(self, session_id: str) -> None:
        """Forget everything granted in a conversation, as when it leaves Manual."""
        with self._lock:
            self._by_session.pop(session_id, None)


class ManualScope:
    """What one turn's gates need: the allowances, whose conversation this is, and whether a screen
    can answer. ``attended`` is read at each question, because the screen binds after the registry."""

    def __init__(
        self,
        allowances: ManualAllowances,
        session_id: Callable[[], str],
        attended: Callable[[], bool],
    ) -> None:
        self.allowances = allowances
        self.session_id = session_id
        self.attended = attended

    def is_allowed(self, key: str) -> bool:
        return self.allowances.allowed(self.session_id(), key)


def command_key(command: str) -> str:
    """The allowance key of one shell command: the exact text, never the shell as a whole.

    Empty (no "in this conversation" offered) for a command too long to have been read.
    """
    key = f"run_shell:{command}"
    return key if len(key) <= MAX_KEY_CHARS else ""


def _norm_target(workspace: Path, raw: str) -> str:
    """The destination as one spelling, so ``./a.txt`` and ``a.txt`` are the same file."""
    inside = _inside(workspace, raw) if raw else None
    if inside is None:
        return raw.strip().replace("\\", "/")
    return inside.relative_to(workspace.resolve()).as_posix()


def _read(path: Path) -> str:
    try:
        if path.is_file() and path.stat().st_size <= _MAX_READ_BYTES:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _inside(workspace: Path, rel: str) -> Path | None:
    try:
        target = (workspace / rel).resolve()
        target.relative_to(workspace.resolve())
    except (OSError, ValueError):
        return None
    return target


def _unified(rel: str, before: str, after: str, *, new_file: bool) -> str:
    lines = difflib.unified_diff(
        [] if new_file else before.splitlines(),
        after.splitlines(),
        fromfile="/dev/null" if new_file else f"a/{rel}",
        tofile=f"b/{rel}",
        lineterm="",
        n=2,
    )
    return "\n".join(lines)


def _edit_diff(workspace: Path, rel: str, old: str, new: str, replace_all: bool) -> str:
    target = _inside(workspace, rel)
    before = _read(target) if target is not None else ""
    if before and old and old in before:
        after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
        return _unified(rel, before, after, new_file=False)
    # The anchor is not in the file (the tool will refuse it): show what was proposed, as written.
    return "\n".join([f"--- {rel}", *(f"-{line}" for line in old.splitlines()), *(f"+{line}" for line in new.splitlines())])


def proposed_diff(workspace: Path, tool: str, args: dict[str, Any]) -> str:
    """The change a write tool is about to make, as a diff, read from the call's own arguments.

    Never writes, and never raises: a call it cannot read shows what it can and the card still asks.
    """
    try:
        if tool == "write_file":
            rel = str(args.get("path", ""))
            target = _inside(workspace, rel)
            before = _read(target) if target is not None else ""
            exists = bool(target is not None and target.is_file())
            return _unified(rel, before, str(args.get("content", "")), new_file=not exists)
        if tool == "edit_file":
            return _edit_diff(
                workspace, str(args.get("path", "")), str(args.get("old", "")),
                str(args.get("new", "")), bool(args.get("replace_all", False)),
            )
        if tool == "apply_patch":
            return str(args.get("patch", ""))
        if tool == "edit_batch":
            parts = []
            for edit in args.get("edits") or []:
                if isinstance(edit, dict):
                    parts.append(_edit_diff(
                        workspace, str(edit.get("path", "")), str(edit.get("old", "")),
                        str(edit.get("new", "")), False,
                    ))
            return "\n".join(parts)
        keys = ("path", "title")
        return "\n".join(f"{key}: {args[key]}" for key in keys if args.get(key))
    except Exception:  # noqa: BLE001 - a preview that fails must not stop the question being asked
        return ""


def _targets_of(spec: WriterSpec, args: dict[str, Any]) -> list[str]:
    """Where the call writes, as the call names it; the tool's default when it names nothing."""
    found: list[str] = []
    for key in spec.target:
        value = args.get(key)
        if key == "edits" and isinstance(value, list):
            found.extend(str(e.get("path", "")) for e in value if isinstance(e, dict))
        elif isinstance(value, str) and value:
            found.append(value)
    found = list(dict.fromkeys(p for p in found if p))
    return found or ([spec.default] if spec.default else [])


def _remote_of(tool: Any) -> Any:
    """The remote file tool under ``tool`` (through wrappers), or None for an ordinary local one.

    A tool of an SSH conversation writes on another machine: the diff of the workspace file of the same
    relative path would be a different file's, and "a write in the workspace" would be false.
    """
    for _ in range(12):
        if tool is None:
            return None
        if isinstance(getattr(tool, "remote_host", None), str):
            return tool
        tool = getattr(tool, "inner", None)
    return None


def _bounded(diff: str) -> str:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    return f"{diff[:MAX_DIFF_CHARS]}\n… [{len(diff) - MAX_DIFF_CHARS} more characters not shown]"


class ManualEditGate(Tool):
    """Asks the owner, on the turn's card, before one workspace-writing tool runs; refuses at once
    with no screen.

    Which tools, and where each writes, come from ``ledger.WORKSPACE_WRITERS``, the one table the
    ledger and the read-only reach read too. ``ask`` is the turn's ``always_ask`` approver: it reads
    ``CHIMERA_APPROVAL_MODE=allow`` as ``ask`` and consults no remembered rule, so Manual's question
    is never answered by a standing yes the owner gave for something else.
    """

    def __init__(
        self, inner: Tool, workspace: Path, scope: ManualScope, ask: Callable[..., bool],
        spec: WriterSpec,
    ) -> None:
        self.inner = inner
        self.workspace = workspace
        self.scope = scope
        self.ask = ask
        self.spec = spec
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters
        self.untrusted_output = inner.untrusted_output

    def __getattr__(self, item: str) -> Any:
        # Anything a wrapper chain reads off the tool it wraps (workspace root, write region, ...).
        if item == "inner":
            raise AttributeError(item)
        return getattr(self.inner, item)

    def _allow_key(self, targets: list[str]) -> str:
        """``tool:path`` for exactly one destination, else "" (only "once" is offered)."""
        if len(targets) != 1:
            return ""
        remote = _remote_of(self.inner)
        if remote is not None:  # the path is the host's, not a workspace path: name the host in the key
            key = f"{self.name}@{remote.remote_host}:{targets[0]}"
            return key if len(key) <= MAX_KEY_CHARS else ""
        key = f"{self.name}:{_norm_target(self.workspace, targets[0])}"
        return key if len(key) <= MAX_KEY_CHARS else ""

    def run(self, **kwargs: Any) -> str:
        spec = self.spec
        if spec.actions is not None and kwargs.get("action") not in spec.actions:
            return self.inner.run(**kwargs)  # a read or a proposal: nothing is written
        if not self.scope.attended():
            return refusal(NEEDS_SCREEN)
        targets = _targets_of(spec, kwargs)
        key = self._allow_key(targets)
        if key and self.scope.is_allowed(key):
            return self.inner.run(**kwargs)
        shown = ", ".join(targets) or "(the tool picks the file)"
        remote = _remote_of(self.inner)
        if remote is not None:
            try:
                preview = remote.remote_preview(kwargs) if spec.diff else ""
            except Exception:  # noqa: BLE001 - a preview that fails must not stop the question being asked
                preview = ""
            diff = _bounded(preview)
            where = f"{self.name} on {remote.remote_host}"
            reason = (
                f"a write on {remote.remote_host} over ssh (not in the workspace), in a conversation "
                "that asks before each one (Manual)"
            )
        else:
            diff = _bounded(proposed_diff(self.workspace, self.name, kwargs)) if spec.diff else ""
            where = self.name
            reason = "a write in the workspace, in a conversation that asks before each one (Manual)"
        action = f"{where}: {shown}" + (f"\n{diff}" if diff else "")
        question = SimpleNamespace(
            action=action,
            reason=reason,
            decision=None,
            allow_key=key,
        )
        if not bool(self.ask(question, action)):
            return refusal(EDIT_DECLINED)
        return self.inner.run(**kwargs)


def install_manual_edit_gate(
    registry: Any, workspace: Path, scope: ManualScope, ask: Callable[..., bool]
) -> list[str]:
    """Wrap every tool ``WORKSPACE_WRITERS`` names that a registry holds. Returns the names wrapped."""
    wrapped: list[str] = []
    for name, spec in WORKSPACE_WRITERS.items():
        if name in registry:
            registry.register(
                ManualEditGate(registry.get(name), workspace, scope, ask, spec), replace=True
            )
            wrapped.append(name)
    return wrapped


def payload_asker(
    workspace: Path, scope: ManualScope, ask: Callable[..., bool]
) -> Callable[[str, int, str], bool]:
    """The question the MCP payload sink asks before it writes a file into the workspace.

    Declined, unattended or refused, the sink stores the payload in the app folder instead, so the
    tool call still returns its result and nothing lands in the project.
    """

    def ask_payload(shown: str, size: int, source: str) -> bool:
        if not scope.attended():
            return False
        key = f"{MCP_PAYLOAD}:{shown}"
        if len(key) > MAX_KEY_CHARS:
            key = ""
        if key and scope.is_allowed(key):
            return True
        action = f"{MCP_PAYLOAD}: {shown}\n{size} bytes from {source}"
        question = SimpleNamespace(
            action=action,
            reason="a file an MCP tool returned, to be saved in the workspace (Manual)",
            decision=None, allow_key=key,
        )
        return bool(ask(question, action))

    return ask_payload
