"""A coding conversation that keeps what it did, not a summary of what it said.

``ChatSession`` is the right shape for chat and the wrong shape for code. It flattens the
conversation into prose — ``"User: …\\nAssistant: …"``, capped at six turns — which is exactly what
a chat assistant needs and exactly what a coding agent cannot survive: every tool call is discarded
between turns, so "now fix the other one" arrives at an agent that has no idea which files it read
five seconds ago, and it reads them all again.

The fix is not to change ``ChatSession``. Its flattened form is the on-disk format of the messaging
gateway, the TUI, ``/v1/chat/completions`` and the benchmarks, and widening ``ChatTurn`` to hold
tool calls would change all of them to fix none of them. So this sits alongside it and keeps the
real thing: ``AgentResult.transcript``, the model's own message list, tool calls included.

What that buys, concretely: turn two starts from the messages of turn one, so the agent knows what
it read, what it wrote, what failed, and what the file looked like afterwards. That is the whole
difference between a coding agent and a chat window that can call tools.

Trimming is where this could quietly break. A conversation grows without bound, but a message list
cannot be cut at an arbitrary point: an assistant message carrying ``tool_calls`` and the ``tool``
results answering it are one unit, and a ``tool`` message whose call is gone is a hard provider
error, not a degradation. So the tail is cut only at a ``user`` message — a boundary that is always
safe, because that is where every turn starts.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from chimera.core.agent import AgentResult, ToolActivity
from chimera.providers.gateway import MessageLike
from chimera.telemetry import get_logger

_log = get_logger("core.code_session")

#: How many messages a session carries into the next turn before the oldest are dropped.
#:
#: This is a *storage* bound, not a context-window one — the agent's own ``context_budget`` decides
#: what fits in a prompt and compacts with restoration when it does not. Both exist because they
#: answer different questions: one is "will this request be accepted", the other is "does this file
#: grow forever". 200 is roughly thirty tool-using turns.
DEFAULT_MAX_MESSAGES = 200


class SupportsCodeRun(Protocol):
    """The agent loop, with the seams a coding turn needs: history in, live callbacks out."""

    def run(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = ...,
        on_tool: Callable[[ToolActivity], None] | None = ...,
        on_edit: Callable[[str, str], None] | None = ...,
        history: list[MessageLike] | None = ...,
        images: list[str] | None = ...,
    ) -> AgentResult: ...


def _title_of(content: str) -> str:
    """A one-line label for a conversation, from the message that started it.

    Newlines collapse to spaces so a pasted stack trace does not become a five-line row, and the cut
    is at 80 characters — long enough to tell two similar questions apart, short enough that the
    list stays a list.
    """
    return " ".join(content.split())[:80]


def _as_dict(message: MessageLike) -> dict[str, Any]:
    """A message as a plain dict, whether it arrived as one or as a ``Message``."""
    return message if isinstance(message, dict) else message.as_dict()


def trim_to_a_safe_boundary(messages: list[MessageLike], limit: int) -> list[MessageLike]:
    """Keep at most ``limit`` messages, cutting only where a turn begins.

    An assistant message with ``tool_calls`` and the ``tool`` messages answering it are indivisible;
    cutting between them leaves an orphan the provider rejects outright. Scanning forward to the
    next ``user`` message keeps slightly more than asked rather than slightly less — the direction
    that fails as a bigger prompt instead of as a 400.

    Returns the list unchanged when it already fits, and returns everything from the last ``user``
    message when no earlier boundary is close enough (a single turn longer than the whole limit).
    """
    if len(messages) <= limit:
        return messages
    for i in range(len(messages) - limit, len(messages)):
        if _as_dict(messages[i]).get("role") == "user":
            return messages[i:]
    for i in range(len(messages) - 1, -1, -1):
        if _as_dict(messages[i]).get("role") == "user":
            return messages[i:]
    return []  # no user message at all: nothing here is a safe place to resume from


@dataclass
class CodeSession:
    """A multi-turn coding conversation over one workspace."""

    agent: SupportsCodeRun
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    workspace: str = ""
    """Which project this conversation is about.

    Stored so a session list can be GROUPED by project. Without it a list of past conversations is
    a flat pile with no owner — you can see that you asked something last Tuesday but not which
    codebase you asked it about, which is most of what makes an old conversation findable.

    Empty means "the server's own workspace", the same convention the request uses, and it is
    deliberately not resolved to an absolute path here: two sessions started from the same relative
    root should group together rather than splitting on how the caller spelled it.
    """
    messages: list[MessageLike] = field(default_factory=list)
    """The conversation in the model's own format, WITHOUT the system message.

    The system message is deliberately absent: the agent rebuilds it every turn so that retrieved
    skills follow the current task and project instructions follow the file currently in focus.
    Carrying a stale one here would pin both to whatever the first turn happened to be about.
    """
    max_messages: int = DEFAULT_MAX_MESSAGES

    def send(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
        on_edit: Callable[[str, str], None] | None = None,
        images: list[str] | None = None,
    ) -> AgentResult:
        """Run one turn with the previous turns as history, and absorb the result.

        ``images`` belong to THIS turn only. They are not stored with the conversation: the
        transcript keeps the text of what was said, and re-sending a picture on every later turn
        would be paid for again each time — for what is, after the first answer, no new information.
        """
        # Forwarded only when there ARE images. `SupportsCodeRun` is a published protocol in an
        # open-source framework, so an implementation written before images existed is a reasonable
        # thing to find in the wild — and passing `images=None` to it would break it for the sake of
        # sending nothing.
        extra: dict[str, Any] = {"images": images} if images else {}
        result = self.agent.run(
            task,
            on_token=on_token,
            on_tool=on_tool,
            on_edit=on_edit,
            history=list(self.messages),
            **extra,
        )
        self.absorb(result)
        return result

    def absorb(self, result: AgentResult) -> None:
        """Replace the conversation with the transcript the run actually produced.

        Replace, not append: the transcript already CONTAINS the history it was handed, plus the
        new turn. Appending it would duplicate the entire conversation each turn — a bug that stays
        invisible until the prompt is four times the size anyone expected.

        A run whose backend returned no transcript (a stub, a failure before the first call) leaves
        the conversation untouched rather than clearing it.
        """
        if not result.transcript:
            _log.debug("session %s: empty transcript, conversation unchanged", self.session_id)
            return
        body = [m for m in result.transcript if _as_dict(m).get("role") != "system"]
        self.messages = trim_to_a_safe_boundary(body, self.max_messages)

    def clear(self) -> None:
        self.messages = []

    # -- persistence ---------------------------------------------------------------------------

    def title(self) -> str:
        """What to call this conversation in a list: the first thing the user asked.

        Derived rather than stored, and derived from the FIRST user message rather than the last:
        a conversation is findable by how it started ("fix the login redirect"), not by whatever it
        drifted to. Nothing is summarised by a model — a title that costs a model call is a title
        that is sometimes missing, and one that paraphrases is a title that can be wrong.
        """
        for message in self.messages:
            data = _as_dict(message)
            if data.get("role") == "user":
                return _title_of(str(data.get("content") or ""))
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "messages": [_as_dict(m) for m in self.messages],
        }


class CodeSessionStore:
    """Durable code sessions, one JSON file each.

    Kept apart from ``SessionStore`` on purpose. That store holds ``ChatTurn`` pairs and backs
    ``/api/sessions`` and the Sessions screen; putting a message list into it would either corrupt
    the readers that expect prose or force a migration on every existing session file, to make a
    screen show a conversation it has no way to render.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, session_id: str) -> Path:
        # The id is generated here (a uuid hex), but this is also reachable from an API parameter,
        # so the filename is derived from the id rather than trusted as one.
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")[:64]
        if not safe:
            raise ValueError("a session id must contain at least one usable character")
        return self.root / f"{safe}.json"

    def save(self, session: CodeSession) -> None:
        path = self._path(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session.to_dict()), encoding="utf-8")

    def load(self, session_id: str, agent: SupportsCodeRun) -> CodeSession:
        """Return the stored session, or a fresh one under that id if there is nothing stored.

        A missing file is the ordinary first-turn case, not an error. A *corrupt* file is treated
        the same way and logged: refusing to start a conversation because an old one is unreadable
        would turn a bad write into a permanently broken session.
        """
        path = self._path(session_id)
        if not path.is_file():
            return CodeSession(agent, session_id=session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
        except (OSError, ValueError) as exc:
            _log.warning("code session %s unreadable, starting fresh: %s", session_id, exc)
            return CodeSession(agent, session_id=session_id)
        return CodeSession(
            agent,
            session_id=session_id,
            workspace=str(data.get("workspace") or ""),
            messages=list(messages),
        )

    def list_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))

    def list_meta(self) -> list[dict[str, Any]]:
        """Every stored conversation, newest first, with just enough to draw a list.

        Reads each file rather than keeping an index. An index is a second source of truth that
        drifts the first time a write is interrupted, and the alternative it saves us from — a few
        hundred small JSON reads — is not a cost anyone will notice on a local machine.

        An unreadable file is SKIPPED rather than raising: one bad write must not make the whole
        list refuse to render, which would look like "you have no conversations".
        """
        if not self.root.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
            except (OSError, ValueError) as exc:
                _log.warning("code session %s unreadable, omitted from the list: %s", path.stem, exc)
                continue
            title = ""
            for message in messages:
                if message.get("role") == "user":
                    title = _title_of(str(message.get("content") or ""))
                    break
            out.append({
                "id": str(data.get("session_id") or path.stem),
                "title": title,
                "workspace": str(data.get("workspace") or ""),
                # Turns, not messages: a user asking twice is two turns, but the transcript between
                # them holds every tool call the agent made, and counting those would report a
                # number that grows with the agent's verbosity rather than with the conversation.
                "turns": sum(1 for m in messages if m.get("role") == "user"),
                "updated_at": path.stat().st_mtime,
            })
        out.sort(key=lambda m: float(m["updated_at"]), reverse=True)
        return out

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.is_file():
            return False
        path.unlink()
        return True
