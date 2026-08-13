"""One turn, driven through somebody else's agent, reported in Chimera's own words.

The translation this file performs is the whole feature: an ACP agent streams `session/update`
notifications, and the Code screen understands `token` / `tool` / `edit` / `done`. Getting that
mapping right is what lets an external worker appear on a screen that was built for the native loop
— same transcript, same receipt, same revert button — instead of as a second, parallel interface.

Where the two vocabularies do not line up, the difference is stated rather than smoothed over:

* **Thoughts are dropped.** `agent_thought_chunk` is reasoning, and the only stream this screen has
  for text is the answer. Folding reasoning into the answer would corrupt the one artefact a user
  quotes and keeps.
* **A tool is reported once, when it finishes.** :func:`chimera.core.events.tool` takes ``ok`` as a
  bool, so there is no shape for "still running". Emitting on start as well would show every call
  twice; emitting only on start would report an outcome nobody knows yet.
* **A diff becomes a patch.** ACP sends ``oldText``/``newText``; the `edit` event carries a unified
  diff, because that is what the screen already renders and what the revert already understands.
"""

from __future__ import annotations

import difflib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.acp.agents import AcpAgentSpec, child_env
from chimera.acp.client import AcpConnection, AcpError
from chimera.telemetry import get_logger
from chimera.tools.workspace import atomic_write_text, resolve_in_workspace
from chimera.tools.write_region import WriteRegion

_log = get_logger("acp.turn")

#: The protocol version this client speaks.
PROTOCOL_VERSION = 1

#: How long to wait for `initialize`. An adapter fetched by `npx` on first use downloads a package
#: before it says anything at all, and a 10-second timeout would report that as a broken install.
CONNECT_TIMEOUT = 180.0

#: How long one prompt may take. A coding turn is minutes, not seconds; the ceiling exists so a
#: wedged agent ends as a failed turn rather than as a request nobody ever answers.
TURN_TIMEOUT = 1800.0

#: Cap on what a `fs/read_text_file` handler will return, matching `ReadFileTool`.
MAX_READ_CHARS = 20_000


@dataclass
class AcpTurnResult:
    """What the external agent did, in the shape the Code screen already reports."""

    answer: str = ""
    stop_reason: str = ""
    tool_names: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)
    #: Writes the agent asked us to make and we refused (outside the write region). Kept because a
    #: refusal the user never sees is indistinguishable from a write that silently did not happen.
    refused: list[str] = field(default_factory=list)
    #: Permissions answered on the user's behalf; see :meth:`AcpTurn._permission`.
    auto_approved: list[str] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    usd: float | None = None


def unified_patch(path: str, old: str, new: str) -> str:
    """A unified diff, which is what the `edit` event carries and the revert understands."""
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(diff)


def _text_of(block: Any) -> str:
    """The text inside a ContentBlock, or "" for a block that carries none (an image, a resource)."""
    if isinstance(block, dict) and block.get("type") == "text":
        return str(block.get("text") or "")
    return ""


class AcpTurn:
    """A live connection to one external agent, good for one session's worth of turns."""

    def __init__(
        self,
        spec: AcpAgentSpec,
        workspace: Path,
        *,
        argv: list[str] | None = None,
        write_region: WriteRegion | None = None,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict[str, Any], bool, str], None] | None = None,
        on_edit: Callable[[str, str], None] | None = None,
        connect_timeout: float = CONNECT_TIMEOUT,
        turn_timeout: float = TURN_TIMEOUT,
    ) -> None:
        self.spec = spec
        # ACP requires `cwd` to be absolute, and it is what every relative path in the session
        # resolves against — including ours, when the agent asks us to read or write one.
        self.workspace = Path(workspace).resolve()
        self.argv = list(argv or spec.argv)
        self.write_region = write_region
        self.connect_timeout = connect_timeout
        self.turn_timeout = turn_timeout
        self._on_token = on_token
        self._on_tool = on_tool
        self._on_edit = on_edit
        self._conn: AcpConnection | None = None
        self._session_id: str | None = None
        self._result = AcpTurnResult()
        #: Tool calls seen but not yet finished, by id — so a `tool_call_update` can be reported
        #: with the title and kind that only arrived on the original `tool_call`.
        self._calls: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # --- lifetime ---------------------------------------------------------------------------

    def __enter__(self) -> AcpTurn:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self) -> AcpTurn:
        """Launch the agent, negotiate, and open a session rooted at the workspace."""
        if not self.argv:
            raise AcpError("no command configured for this agent")
        self._conn = AcpConnection(
            self.argv,
            cwd=self.workspace,
            env=child_env(self.spec),
            handlers={
                "fs/read_text_file": self._read_text_file,
                "fs/write_text_file": self._write_text_file,
                "session/request_permission": self._permission,
            },
            on_notify=self._notify,
            name=self.spec.key,
        )
        try:
            self._conn.start()
        except FileNotFoundError as exc:
            raise AcpError(
                f"{self.spec.label} is not installed — {self.spec.install_hint}"
            ) from exc

        self._conn.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                # Offered, not enforced. The agents worth driving have file tools of their own and
                # may use them instead; declaring these means "you MAY route through us", which is
                # the strongest true statement available here.
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    # Declined deliberately: a terminal we host would be a second execution path
                    # beside the sandbox, with none of its rules. The agent runs commands its own
                    # way, and the posture note says so rather than this pretending otherwise.
                    "terminal": False,
                },
                "clientInfo": {"name": "chimera", "title": "Chimera", "version": _version()},
            },
            timeout=self.connect_timeout,
        )
        session = self._conn.request(
            "session/new",
            {"cwd": str(self.workspace), "mcpServers": []},
            timeout=self.connect_timeout,
        )
        self._session_id = str((session or {}).get("sessionId") or "")
        if not self._session_id:
            raise AcpError(f"{self.spec.label} did not open a session")
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def is_alive(self) -> bool:
        """Whether this connection can still take a prompt.

        Asked between turns, because a conversation's agent can die while nobody is looking — an
        update, a crash, a machine that slept. From the conversation's side the next message should
        just work, so the registry replaces a dead agent rather than reporting one.
        """
        return self._conn is not None and self._conn.exit_code is None and bool(self._session_id)

    def rebind(
        self,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict[str, Any], bool, str], None] | None = None,
        on_edit: Callable[[str, str], None] | None = None,
    ) -> None:
        """Point the callbacks at the current request's event stream.

        A connection outlives the turn that opened it, and the callbacks do not: they write into an
        SSE queue that belongs to one HTTP request. Left bound to the first turn, the second turn's
        tokens would be posted to a queue nobody is reading — the screen would show a live agent
        producing nothing.
        """
        self._on_token = on_token
        self._on_tool = on_tool
        self._on_edit = on_edit

    def cancel(self) -> None:
        """Ask the agent to stop the current turn. A notification, so it cannot itself block."""
        if self._conn is not None and self._session_id:
            self._conn.notify("session/cancel", {"sessionId": self._session_id})

    # --- the turn ---------------------------------------------------------------------------

    def prompt(self, text: str, *, images: list[str] | None = None) -> AcpTurnResult:
        """Send one message and return once the agent says the turn is over."""
        if self._conn is None or not self._session_id:
            raise AcpError("the session is not open")
        self._result = AcpTurnResult()
        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for path in images or []:
            block = _image_block(Path(path))
            if block is not None:
                blocks.append(block)
        try:
            reply = self._conn.request(
                "session/prompt",
                {"sessionId": self._session_id, "prompt": blocks},
                timeout=self.turn_timeout,
            )
        except AcpError:
            # A turn that died mid-flight still edited whatever it edited. Losing the record of that
            # would leave a workspace changed by a turn that reports nothing.
            self._result.stop_reason = "error"
            raise
        self._result.stop_reason = str((reply or {}).get("stopReason") or "end_turn")
        return self._result

    # --- notifications ------------------------------------------------------------------------

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        if method != "session/update":
            return
        update = params.get("update")
        if not isinstance(update, dict):
            return
        kind = str(update.get("sessionUpdate") or "")
        if kind == "agent_message_chunk":
            chunk = _text_of(update.get("content"))
            if chunk and self._on_token is not None:
                self._on_token(chunk)
            self._result.answer += chunk
        elif kind == "tool_call":
            call_id = str(update.get("toolCallId") or "")
            if call_id:
                self._calls[call_id] = dict(update)
        elif kind == "tool_call_update":
            self._tool_update(update)
        elif kind == "usage_update":
            self._usage(update)
        # `plan`, `agent_thought_chunk`, `available_commands_update` and the rest are not dropped
        # for lack of interest — they have no counterpart in this screen's vocabulary yet, and
        # inventing one here would put reasoning into the answer.

    def _tool_update(self, update: dict[str, Any]) -> None:
        call_id = str(update.get("toolCallId") or "")
        started = self._calls.get(call_id, {})
        status = str(update.get("status") or started.get("status") or "")
        contents = update.get("content")
        if isinstance(contents, list):
            for item in contents:
                self._maybe_edit(item)
        if status not in ("completed", "failed", "cancelled"):
            return
        self._calls.pop(call_id, None)
        title = str(update.get("title") or started.get("title") or started.get("kind") or "tool")
        name = str(started.get("kind") or update.get("kind") or "other")
        observation = "\n".join(
            _text_of(c.get("content")) for c in (contents or []) if isinstance(c, dict)
        ).strip()
        self._result.tool_names.append(name)
        if self._on_tool is not None:
            self._on_tool(f"{name}: {title}" if title else name, {}, status == "completed", observation)

    def _maybe_edit(self, item: Any) -> None:
        """A `diff` content block is this screen's `edit` event, once it becomes a patch."""
        if not isinstance(item, dict) or item.get("type") != "diff":
            return
        path = str(item.get("path") or "")
        if not path:
            return
        relative = self._relative(path)
        patch = unified_patch(relative, str(item.get("oldText") or ""), str(item.get("newText") or ""))
        if relative not in self._result.edited:
            self._result.edited.append(relative)
        if self._on_edit is not None:
            self._on_edit(relative, patch)

    def _usage(self, update: dict[str, Any]) -> None:
        used = update.get("used")
        if isinstance(used, (int, float)):
            self._result.completion_tokens = int(used)
        cost = update.get("cost")
        if isinstance(cost, dict) and isinstance(cost.get("amount"), (int, float)):
            self._result.usd = float(cost["amount"])

    # --- the methods the agent may call on us --------------------------------------------------

    def _read_text_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path = resolve_in_workspace(self.workspace, str(params.get("path") or ""))
        if not path.is_file():
            raise AcpError(f"file not found: {params.get('path')}")
        text = path.read_text(encoding="utf-8", errors="replace")
        line = params.get("line")
        limit = params.get("limit")
        if isinstance(line, int) and line > 0:
            lines = text.splitlines(keepends=True)[line - 1 :]
            if isinstance(limit, int) and limit > 0:
                lines = lines[:limit]
            text = "".join(lines)
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        return {"content": text}

    def _write_text_file(self, params: dict[str, Any]) -> dict[str, Any]:
        """Honour a write, or refuse it — and either way, report it as an edit.

        `resolve_in_workspace` raises on an escape and the write region refuses a path outside its
        globs, exactly as they do for the native tools. What is different, and what the posture note
        must say, is that reaching this handler at all is the agent's choice: an agent with its own
        file tools can write without asking, and then this refusal protects nothing.
        """
        raw = str(params.get("path") or "")
        target = resolve_in_workspace(self.workspace, raw)
        relative = self._relative(str(target))
        if self.write_region is not None and (error := self.write_region.check(target)):
            self._result.refused.append(relative)
            raise AcpError(error)
        content = str(params.get("content") or "")
        old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, content)
        if relative not in self._result.edited:
            self._result.edited.append(relative)
        if self._on_edit is not None:
            self._on_edit(relative, unified_patch(relative, old, content))
        return {}

    def _permission(self, params: dict[str, Any]) -> dict[str, Any]:
        """Answer a permission prompt by allowing it once, and RECORDING that we did.

        Refusing by default would make every turn useless. Prompting the user is the obvious third
        answer and it is the wrong one here, for a reason worth writing down: the agents this drives
        have their own file and shell tools, so a prompt we gate is a prompt the agent did not have
        to ask. A gate that can be walked around is not a gate; presenting one would sell a
        protection that does not exist.

        So the honest posture is the one the receipt can stand behind: allow, record every grant,
        and rely on the checkpoint — which is real, and which covers the writes that never asked.
        """
        options = params.get("options")
        chosen = ""
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict) and option.get("kind") == "allow_once":
                    chosen = str(option.get("optionId") or "")
                    break
            if not chosen and options and isinstance(options[0], dict):
                chosen = str(options[0].get("optionId") or "")
        call = params.get("toolCall")
        label = ""
        if isinstance(call, dict):
            label = str(call.get("title") or call.get("toolCallId") or "")
        with self._lock:
            self._result.auto_approved.append(label or "a tool call")
        if not chosen:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": chosen}}

    # --- helpers ------------------------------------------------------------------------------

    def _relative(self, path: str) -> str:
        try:
            return Path(path).resolve().relative_to(self.workspace).as_posix()
        except ValueError:
            return path


def _image_block(path: Path) -> dict[str, Any] | None:
    """An attached image as an ACP content block, or None when it cannot be read."""
    import base64
    import mimetypes

    try:
        data = path.read_bytes()
    except OSError:
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return {"type": "image", "mimeType": mime, "data": base64.b64encode(data).decode("ascii")}


def _version() -> str:
    from chimera import __version__

    return __version__
