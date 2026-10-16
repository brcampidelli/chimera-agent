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
import os
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from chimera.core.agent import AgentResult, ToolActivity, is_guidance
from chimera.core.code_session_marks import CodeSessionMarks
from chimera.core.context_budget import is_compaction_note, restored_task
from chimera.core.redact import redact_json
from chimera.providers.gateway import MessageLike
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.orchestration.budget import SpendBudget

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


def _opens_turn(message: MessageLike) -> bool:
    """A user message that starts a turn — not guidance typed into one already running. Counting
    guidance as a turn would split one turn in two: the title, the turn count and the cut point
    for a long conversation all read "user" as "a turn began here"."""
    data = _as_dict(message)
    return data.get("role") == "user" and not is_guidance(data)


def _is_person_turn(message: MessageLike) -> bool:
    """A turn the person opened: not guidance, and not a note compaction left in the transcript."""
    data = _as_dict(message)
    return _opens_turn(data) and not is_compaction_note(data.get("content"))


def _title_from(messages: Sequence[MessageLike]) -> str:
    """The conversation's title: the first thing the person asked.

    Once a conversation compacts, that message is gone and the first user message is compaction's
    own note. The restored block right after it carries the task verbatim, so the title is taken from
    there; without one, from the first message the person typed that is still in the transcript.
    """
    for message in messages:
        data = _as_dict(message)
        if data.get("role") != "user":
            continue
        task = restored_task(data.get("content"))
        if task:
            return _title_of(task)
        if _is_person_turn(data):
            return _title_of(str(data.get("content") or ""))
    return ""


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
        if _opens_turn(messages[i]):
            return messages[i:]
    for i in range(len(messages) - 1, -1, -1):
        if _opens_turn(messages[i]):
            return messages[i:]
    return []  # no user message at all: nothing here is a safe place to resume from


#: The owner's switches for a conversation's pull request, both OFF until the owner turns them on.
PULL_REQUEST_OPTIONS = ("fix_on_ci_failure", "cleanup_on_merge")


def pull_request_record(raw: object) -> dict[str, Any] | None:
    """A stored pull request record, validated, or None when there is none or it is unusable.

    Read from a file an older or newer version may have written, so nothing in it is trusted: the
    number must be a positive int (a bool is an int in Python and is refused), the URL a string, and
    each option is ON only when stored as exactly ``True`` — anything else reads as the default, off.
    """
    if not isinstance(raw, dict):
        return None
    number = raw.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    url = raw.get("url")
    record: dict[str, Any] = {"number": number, "url": url[:2048] if isinstance(url, str) else ""}
    for option in PULL_REQUEST_OPTIONS:
        record[option] = raw.get(option) is True
    return record


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
    pull_request: dict[str, Any] | None = None
    """The pull request opened from this conversation's project, with the owner's two options.

    Optional and absent from every file written before it existed, which reads as "no PR". Written
    only through :meth:`CodeSessionStore.record_pull_request` and
    :meth:`CodeSessionStore.set_pull_request_options`, never by a turn: a turn holds the copy it
    loaded when it started, and :meth:`CodeSessionStore.save` keeps the stored record over it, so a
    turn that ran while the owner opened the PR cannot erase the record by saving its transcript.
    """

    def send(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
        on_edit: Callable[[str, str], None] | None = None,
        on_todo: Callable[[list[dict[str, str]]], None] | None = None,
        on_notice: Callable[[str, str, dict[str, Any]], None] | None = None,
        images: list[str] | None = None,
        should_stop: Callable[[], bool] | None = None,
        spend: SpendBudget | None = None,
        take_guidance: Callable[[], list[str]] | None = None,
    ) -> AgentResult:
        """Run one turn with the previous turns as history, and absorb the result.

        ``spend`` is the turn's meter when the caller owns it (the app's, which also sums what the
        turns running at once spend). Forwarded like ``should_stop``: only when given, and only to
        an agent whose ``run`` declares it.

        ``should_stop`` is the agent loop's own cooperative stop, polled once per step — what a
        background work is stopped by from the screen or by the voice. Forwarded only when given
        and only to an agent whose ``run`` declares it, like ``on_todo`` below.

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
        # Same reason as on_todo above: only an agent whose run declares it can be handed it.
        if on_notice is not None and _accepts(self.agent.run, "on_notice"):
            extra["on_notice"] = on_notice
        if should_stop is not None and _accepts(self.agent.run, "should_stop"):
            extra["should_stop"] = should_stop
        if spend is not None and _accepts(self.agent.run, "spend"):
            extra["spend"] = spend
        if take_guidance is not None and _accepts(self.agent.run, "take_guidance"):
            extra["take_guidance"] = take_guidance
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

    def replace_last_answer(self, text: str) -> bool:
        """Put ``text`` where the last turn's final answer is — what shipped, when a check after the
        run replaced the draft (`chimera/fusion/verified.py`). Only the final assistant message, and
        only one with no tool calls: an answer is never spliced into the middle of a tool exchange.
        Returns whether anything was replaced."""
        if not self.messages:
            return False
        last = _as_dict(self.messages[-1])
        if last.get("role") != "assistant" or last.get("tool_calls"):
            return False
        self.messages[-1] = {**last, "content": text}
        return True

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
        return _title_from(self.messages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "messages": [_as_dict(m) for m in self.messages],
            "receipts": self.receipts[-MAX_RECEIPTS:],
            **({"pull_request": self.pull_request} if self.pull_request else {}),
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
        # Beside the folder, not in it: `list_meta` reads every `*.json` in the folder as a
        # conversation. What the list derives a conversation's state from, and its archive.
        self.marks = CodeSessionMarks(self.root.with_name(f"{self.root.name}.marks.json"))
        # Serialises this store's writes of one file against each other: the PR record is a
        # read-modify-write of the stored document, and a turn's save landing between its read and
        # its write would otherwise drop either the record or the turn.
        self._write_lock = threading.RLock()

    def _path(self, session_id: str) -> Path:
        # The id is generated here (a uuid hex), but this is also reachable from an API parameter,
        # so the filename is derived from the id rather than trusted as one.
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")[:64]
        if not safe:
            raise ValueError("a session id must contain at least one usable character")
        return self.root / f"{safe}.json"

    def save(self, session: CodeSession) -> None:
        # Redact strings before JSON serialization: redacting serialized text can consume escaped
        # quotes or newlines inside a credential header and leave the session invalid JSON. The file
        # keeps every tool observation, including provider error bodies that can echo prompt content.
        #
        # The cost, stated rather than hidden: this store is read BACK to continue a conversation, so
        # a credential a user pasted into the chat comes back as `[redacted]` after a restart. Within
        # the turn nothing changes — the messages are in memory unmasked — and a key does not belong
        # in a transcript that outlives the session anyway. The patterns are narrow by design (six
        # credential shapes plus this process's own environment secrets), so ordinary code and prose
        # pass through untouched.
        path = self._path(session.session_id)
        with self._write_lock:
            document = session.to_dict()
            # The stored PR record wins over the session's copy (see `CodeSession.pull_request`):
            # only the two store methods below change it, and a turn saving its transcript is not
            # one of them.
            stored = self._stored_pull_request(path)
            if stored is not None:
                document["pull_request"] = stored
            self._write(path, redact_json(document))

    def _write(self, path: Path, document: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: written beside the file, then put in its place. A reader that met half a file used
        # to read it as unreadable, and `load` starts an unreadable conversation fresh, so a list or a
        # next turn landing mid-save could see the conversation vanish or restart.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(json.dumps(document), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _read_document(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _stored_pull_request(self, path: Path) -> dict[str, Any] | None:
        data = self._read_document(path)
        return pull_request_record(data.get("pull_request")) if data is not None else None

    def pull_request_of(self, session_id: str) -> dict[str, Any] | None:
        """The PR recorded on a stored conversation, or None (no conversation, or no PR)."""
        return self._stored_pull_request(self._path(session_id))

    def record_pull_request(self, session_id: str, *, number: int, url: str) -> bool:
        """Write the PR the owner just opened onto a stored conversation; False when there is none.

        Only an EXISTING conversation is written: creating a file here would conjure a conversation
        with no turns into the sidebar. Recording the same number again keeps the owner's options;
        a different number is a different PR, and options given for one PR are not consent for the
        next, so they go back to off.
        """
        record = pull_request_record({"number": number, "url": url})
        if record is None:
            return False
        path = self._path(session_id)
        with self._write_lock:
            data = self._read_document(path)
            if data is None:
                return False
            before = pull_request_record(data.get("pull_request"))
            if before is not None and before["number"] == number:
                record.update({k: before[k] for k in PULL_REQUEST_OPTIONS})
            data["pull_request"] = record
            self._write(path, data)
        return True

    def set_pull_request_options(
        self, session_id: str, options: dict[str, bool]
    ) -> dict[str, Any] | None:
        """Change the owner's options on a conversation's PR; None when no PR is recorded.

        Unknown keys are ignored rather than stored, so this cannot become a way to write arbitrary
        fields into the transcript file.
        """
        path = self._path(session_id)
        with self._write_lock:
            data = self._read_document(path)
            record = pull_request_record(data.get("pull_request")) if data is not None else None
            if data is None or record is None:
                return None
            for option in PULL_REQUEST_OPTIONS:
                if option in options:
                    record[option] = options[option] is True
            data["pull_request"] = record
            self._write(path, data)
        return record

    def load_existing(self, session_id: str, agent: SupportsCodeRun) -> CodeSession | None:
        """The stored session, or None when nothing readable is stored under this id.

        Unlike `load`, which answers a fresh conversation for a missing or unreadable file (right
        for a first turn), this is for bringing an existing session object up to date, where a fresh
        one would silently replace a real conversation with an empty one.
        """
        if not self._path(session_id).is_file():
            return None
        stored = self.load(session_id, agent)
        has = stored.messages or stored.receipts or stored.workspace or stored.pull_request
        return stored if has else None

    def refresh(self, session: CodeSession) -> None:
        """Bring ``session`` up to what is stored now: its messages, receipts and, if it has none,
        its workspace. Called under the session's lock, just before a turn uses or writes it.

        A turn loads its conversation when the request arrives and may wait for the lock behind
        another turn of the same conversation. Without this it then ran on the history it loaded
        before the other turn finished, and its save erased that turn's exchange.
        """
        stored = self.load_existing(session.session_id, session.agent)
        if stored is None:
            return
        session.messages = stored.messages
        session.receipts = stored.receipts
        session.pull_request = stored.pull_request
        if not session.workspace:
            session.workspace = stored.workspace

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
            # Absent in every file written before C1, which is the ordinary "no PR" case.
            pull_request=pull_request_record(data.get("pull_request")),
        )

    def stored_workspace(self, session_id: str) -> str:
        """The folder a stored conversation belongs to, or "" (none stored, unreadable, or the
        server's own folder — "" is the request's convention for "yours")."""
        try:
            path = self._path(session_id)
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            return ""
        return str(data.get("workspace") or "") if isinstance(data, dict) else ""

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
            title = _title_from(messages)
            # The last stored receipt's verdict: the one fact about how the last turn ended that a
            # conversation older than the marks file (`code_session_marks`) carries on its own.
            receipts = data.get("receipts")
            last = receipts[-1] if isinstance(receipts, list) and receipts else None
            verified = last.get("verified") if isinstance(last, dict) else None
            last_verdict = str(verified.get("state") or "") if isinstance(verified, dict) else ""
            out.append(
                {
                    "id": str(data.get("session_id") or path.stem),
                    "title": title,
                    "workspace": str(data.get("workspace") or ""),
                    # Turns, not messages: a user asking twice is two turns, but the transcript between
                    # them holds every tool call the agent made, and counting those would report a
                    # number that grows with the agent's verbosity rather than with the conversation.
                    "turns": sum(1 for m in messages if _is_person_turn(m)),
                    "updated_at": path.stat().st_mtime,
                    "last_verdict": last_verdict,
                }
            )
        out.sort(key=lambda m: float(m["updated_at"]), reverse=True)
        return out

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.is_file():
            return False
        path.unlink()
        self.marks.forget(session_id)
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
        # The one field a fork does NOT carry: the PR and its options belong to the conversation
        # that opened it. Two conversations holding one PR would be two places able to archive and
        # clean up the same worktree, and the owner switched the option on in only one of them.
        data.pop("pull_request", None)
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
