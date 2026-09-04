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

import inspect
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from chimera.core.agent import AgentResult, ToolActivity
from chimera.core.redact import redact
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

#: How many turn receipts a session keeps. Generous against ``DEFAULT_MAX_MESSAGES`` on purpose:
#: a receipt is a few hundred bytes against a turn's several kilobytes of messages, and having more
#: receipts than displayable turns is harmless — the extra ones simply have no exchange to attach
#: to. Having FEWER would be the damaging direction, because tail-pairing would then leave the
#: newest turns without the accounting somebody is most likely to want.
MAX_RECEIPTS = 400


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
    receipts: list[dict[str, Any]] = field(default_factory=list)
    """One receipt per completed turn, oldest first — what the turn cost, what stopped it, what it
    verified.

    Kept beside the messages rather than inside them: ``messages`` is the model's own format and
    goes to the provider verbatim, so a key of ours in there is a key some provider will one day
    reject. Kept at all because the receipt is the app's answer to "what just happened and what did
    it cost", and until now it existed only in the live stream — reopening a conversation showed
    the words and threw away the accounting.

    Paired with exchanges from the END, never by index from the start. Both lists are trimmed at
    the front as a conversation grows, and the last receipt always belongs to the last turn;
    counting forward would silently shift every receipt onto the wrong turn the first time a
    conversation got long enough to trim.
    """

    def send(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
        on_edit: Callable[[str, str], None] | None = None,
        on_todo: Callable[[list[dict[str, str]]], None] | None = None,
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
        # `on_todo` joins `images` in the conditional block for the reason written above it, and
        # it is the newer of the two: `SupportsCodeRun` is published, so an implementation that
        # predates the task list must not be handed a keyword it never declared.
        #
        # The signature is READ rather than assumed, which the same seam in `AutonomousAgent.
        # _run_worker` already does for `on_edit`/`on_tool`. Writing the rule as a comment and
        # forwarding unconditionally is how the first draft of this looked: the prose said an
        # older implementation was safe, and every stub agent in the suite raised TypeError on
        # the first turn. The `**kwargs`-only case is covered too, by asking whether the
        # parameter is named — a callable that swallows anything accepts it either way.
        extra: dict[str, Any] = {"images": images} if images else {}
        if on_todo is not None and _accepts(self.agent.run, "on_todo"):
            extra["on_todo"] = on_todo
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
            "receipts": self.receipts[-MAX_RECEIPTS:],
        }

    def remember_receipt(self, receipt: dict[str, Any]) -> None:
        """Record what one finished turn cost. Bounded, and never raises.

        A receipt is a record ABOUT a turn that already happened and was already paid for; failing
        to store one must not be able to fail the turn, so a value that will not serialise is
        dropped with a log rather than thrown.
        """
        try:
            json.dumps(receipt)
        except (TypeError, ValueError) as exc:
            _log.debug("session %s: unserialisable receipt dropped (%s)", self.session_id, exc)
            return
        self.receipts.append(receipt)
        del self.receipts[:-MAX_RECEIPTS]


def _accepts(run: Any, name: str) -> bool:
    """Whether ``run`` DECLARES a keyword called ``name``. A ``**kwargs`` catch-all does not count.

    Counting the catch-all is the tempting reading and it is wrong, because a callable that swallows
    every keyword usually forwards them to something that does not. Measured: `_EditingAgent` in the
    API suite is `run(self, task, **kw)` calling `super().run(task, **kw)`, and the parent names four
    callbacks and not this one — so "it accepts anything" meant "it raises TypeError one frame
    deeper", and six turns died before their first tool call.

    The alternative is what `AutonomousAgent._run_worker` does: forward, catch TypeError, retry
    plain. That is right there, where the retry costs one more model call. Here the turn may already
    have written files and paid for tool calls before the TypeError, so a blind second attempt would
    do that work twice. A named parameter is explicit support; anything else runs without the frames.
    """
    try:
        params = inspect.signature(run).parameters
    except (TypeError, ValueError):  # unintrospectable callable (C impl / odd wrapper)
        return False
    return name in params


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
        # Written through `redact`, the same way `steplog.py` has always written its trace, and for
        # the same reason: this file keeps every tool observation of the conversation, and a tool
        # that failed reports back the provider's own error body. That body has been measured to
        # carry an echoed fragment of the prompt and the provider's internal routing trace.
        #
        # The cost, stated rather than hidden: this store is read BACK to continue a conversation, so
        # a credential a user pasted into the chat comes back as `[redacted]` after a restart. Within
        # the turn nothing changes — the messages are in memory unmasked — and a key does not belong
        # in a transcript that outlives the session anyway. The patterns are narrow by design (six
        # credential shapes plus this process's own environment secrets), so ordinary code and prose
        # pass through untouched.
        path = self._path(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(redact(json.dumps(session.to_dict())), encoding="utf-8")

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
            # Absent in every file written before receipts existed, and that is the ordinary case
            # rather than a defect: an old conversation has no accounting to show, and saying so by
            # showing none is right.
            receipts=[r for r in data.get("receipts", []) if isinstance(r, dict)],
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
                _log.warning(
                    "code session %s unreadable, omitted from the list: %s", path.stem, exc
                )
                continue
            title = ""
            for message in messages:
                if message.get("role") == "user":
                    title = _title_of(str(message.get("content") or ""))
                    break
            out.append(
                {
                    "id": str(data.get("session_id") or path.stem),
                    "title": title,
                    "workspace": str(data.get("workspace") or ""),
                    # Turns, not messages: a user asking twice is two turns, but the transcript between
                    # them holds every tool call the agent made, and counting those would report a
                    # number that grows with the agent's verbosity rather than with the conversation.
                    "turns": sum(1 for m in messages if m.get("role") == "user"),
                    "updated_at": path.stat().st_mtime,
                }
            )
        out.sort(key=lambda m: float(m["updated_at"]), reverse=True)
        return out

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def delete_project(self, workspace: str) -> int:
        """Forget every conversation filed under one project, and return how many went.

        **The folder on disk is not touched.** A "project" here is a grouping: the sidebar files
        conversations by the workspace each one recorded, and nothing else about it is stored. So
        the only thing there is to delete is the transcripts, and deleting the user's source code
        because they tidied a sidebar is not a behaviour that gets a second chance to be wrong.

        Matched EXACTLY against the stored string, because that is what the list groups by: two
        spellings of the same folder show as two rows, and this must remove the row that was
        clicked rather than everything that resolves to the same place.
        """
        if not workspace.strip() or not self.root.is_dir():
            return 0
        gone = 0
        for meta in self.list_meta():
            if meta["workspace"] == workspace:
                gone += int(self.delete(str(meta["id"])))
        return gone

    def fork(self, session_id: str) -> str | None:
        """Copy a stored conversation to a new id and return it, or ``None`` if there is none.

        A conversation is a linear message list, so trying an idea in one costs the thread you were
        on: the transcript that comes back replaces what was there, and there is no way back to the
        turn before. Branching is the answer, and the honest form of it is a *copy* — the two
        conversations share a past and nothing else, so a turn in the fork cannot reach back into
        the original the way any shared-state scheme eventually does.

        Copied at the document level rather than through ``CodeSession``: a fork needs no agent,
        makes no model call, and must preserve fields this class does not know about, so a session
        written by a newer version does not lose them by round-tripping through today's fields.
        """
        source = self._path(session_id)
        if not source.is_file():
            return None
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _log.warning("code session %s unreadable, not forked: %s", session_id, exc)
            return None
        if not isinstance(data, dict):
            return None
        new_id = uuid.uuid4().hex
        data["session_id"] = new_id
        target = self._path(new_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
        return new_id

    def raw(self, session_id: str) -> str | None:
        """The stored file's text exactly as it is on disk, or ``None`` when there is no such file.

        Deliberately not parsed and re-serialised. The reason to look at this at all is that
        something about a conversation is not what you expected — a turn missing, a tool result
        orphaned by trimming, a file that ``load()`` quietly gave up on — and a copy that has been
        through ``json.loads`` is a copy of what the parser accepted, which is the very thing in
        question.
        """
        path = self._path(session_id)
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            _log.warning("code session %s could not be read: %s", session_id, exc)
            return None
