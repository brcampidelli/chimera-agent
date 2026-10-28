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
from chimera.core.agent_batch_note import batch_note_message, is_batch_note
from chimera.core.bypass_switches import BypassSwitches, open_switches
from chimera.core.checkout import Checkout, checkout_of
from chimera.core.code_session_marks import (
    CodeSessionMarks,
    ConversationMark,
    merge_pull_requests,
    pull_request_record,
)
from chimera.core.context_budget import is_compaction_note, restored_task
from chimera.core.conversation_title import choose_title
from chimera.core.redact import redact_json
from chimera.providers.gateway import MessageLike
from chimera.telemetry import get_logger

#: The switches this server uses. Memory only until the API says where they are saved
#: (:func:`load_session_bypass`); see ``chimera/core/bypass_switches.py`` for why the disk is never
#: the authority for them.
_ACTIVE: BypassSwitches = BypassSwitches(None)


def load_session_bypass(path: Path | None) -> None:
    """Use the switches saved at ``path``: read once per process, shared by every reader after that."""
    global _ACTIVE
    _ACTIVE = open_switches(path)


def active_switches() -> BypassSwitches:
    return _ACTIVE


def session_bypass(session_id: str) -> bool:
    """Whether the conversation's own switch is on in memory (a saved one is not, until confirmed)."""
    return _ACTIVE.conversation_on(session_id)


def set_session_bypass(session_id: str, enabled: bool) -> None:
    """Switch one conversation on or off. Raises :class:`BypassSaveError` with memory unchanged."""
    _ACTIVE.set_conversation(session_id, enabled)


def forget_session_bypass(session_id: str) -> None:
    """The conversation is gone: its switch goes with it."""
    _ACTIVE.forget_conversation(session_id)


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
    """A deterministic, human-sized label for the first task in a conversation.

    Bridge prompts can prepend standard operating instructions. Skip those complete lines rather
    than letting an implementation directive become the conversation's name; preserve study/task
    identifiers because they are meaningful to the person who wrote them. The cut is at a word
    boundary so the last visible word remains useful.
    """
    lines = [" ".join(line.split()) for line in content.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    def is_instruction(line: str) -> bool:
        folded = line.casefold()
        if folded.startswith("read-only task"):
            return True
        # "Read this function..." is a real request; only a line that points at instruction files
        # is a launcher preamble.
        return folded.startswith(("leia ", "read ")) and any(
            marker in folded for marker in (".md", "instruc", "instruç")
        )

    # Some launchers send the original task title after their mandatory preamble, while a normal
    # conversation may begin with "Read this function...". Skip the preamble only when a later
    # non-instruction line exists; otherwise the person's only request remains a valid title.
    instruction_count = 0
    while instruction_count < len(lines) and is_instruction(lines[instruction_count]):
        instruction_count += 1
    if instruction_count and instruction_count < len(lines):
        lines = lines[instruction_count:]

    # Titles have always represented the whole first request, not just its first physical line.
    # Flatten pasted questions and stack traces before applying the sidebar's length limit.
    title = " ".join(lines)
    if len(title) <= 80:
        return title
    shortened = title[:80].rsplit(" ", 1)[0]
    if shortened == title[:80]:
        shortened = title[:79].rsplit(" ", 1)[0]
    return shortened or title[:80]


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
    selected_model: str = ""
    """The model (and optional ``#low|#medium|#high`` variant) the owner PINNED for this conversation.

    Empty means the conversation follows whatever the install's default is at each turn. Written
    only by a turn that says its model was chosen (``CodeTurnRequest.model_pinned``) — never from
    a request's model alone, because a model that merely arrived on the first turn is not a choice,
    and recording it as one would freeze that turn's default into the conversation forever.
    """
    bypass_permissions: bool = False
    pin_changed: bool = field(default=False, init=False, repr=False, compare=False)
    """This turn set :attr:`selected_model` (:meth:`pin`), so :meth:`CodeSessionStore.refresh` keeps
    it. Otherwise refresh takes the stored pin — including another turn's newer one — and a turn
    that unpinned (``""``) is not undone by the old value still on disk."""
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
    """The LATEST pull request opened from this conversation, with the owner's two options — a
    read-only copy of what the marks file holds when the session was loaded.

    Never written into the transcript (:meth:`to_dict` leaves it out): the record lives beside the
    transcripts (``CodeSessionMarks``), where the agent's tool and the owner's panel both write it,
    and changes only through :meth:`CodeSessionStore.record_pull_request` and
    :meth:`CodeSessionStore.set_pull_request_options`. A turn holds the copy it loaded when it
    started, so a turn saving its transcript cannot erase a PR the owner opened meanwhile — there is
    nothing of it in what the turn writes.
    """
    checkout: Checkout | None = None
    """Which repository and branch :attr:`workspace` was a checkout of, the last time a turn ran in it
    (:meth:`stamp_checkout`).

    Stamped rather than derived on every read, because the moment that most needs it is after the
    folder is gone: a conversation run in a ``git worktree add`` folder must stay filed under its
    repository once that worktree is removed, and a folder that no longer exists cannot say what it
    was. None for a conversation no turn has stamped yet (every file written before this field).
    """

    def stamp_checkout(self, folder: str | Path) -> None:
        """Record what ``folder`` is a checkout of — unless that cannot be read now, in which case
        the previous stamp stays: an unreadable answer is not a newer one."""
        found = checkout_of(folder)
        if found is not None:
            self.checkout = found

    def send(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
        on_tool_start: Callable[[str, dict[str, Any], float], None] | None = None,
        on_context: Callable[[int, int | None], None] | None = None,
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
        if on_tool_start is not None and _accepts(self.agent.run, "on_tool_start"):
            extra["on_tool_start"] = on_tool_start
        if on_context is not None and _accepts(self.agent.run, "on_context"):
            extra["on_context"] = on_context
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
        # A batch's record is not this turn's answer, even when it landed last.
        if last.get("role") != "assistant" or last.get("tool_calls") or is_batch_note(last):
            return False
        self.messages[-1] = {**last, "content": text}
        return True

    def note_agent_batch(self, record: dict[str, Any]) -> None:
        """Record a finished parallel batch this conversation started (`agent_batch_note`).

        Appended as it is, after whatever is last: the batch ran beside the conversation, so it
        belongs after the exchange that confirmed it, and the next turn reads it as context. Not
        trimmed here; the next turn's absorb bounds the list as it always does.
        """
        self.messages.append(batch_note_message(record))

    def pin(self, model: str) -> None:
        """Pin ``model`` for this conversation (``""`` unpins: follow the install default again)."""
        self.selected_model = model
        self.pin_changed = True

    def clear(self) -> None:
        self.messages = []

    # -- persistence ---------------------------------------------------------------------------

    def title(self) -> str:
        """The conversation's title derived from its messages: the first thing the user asked.

        The LIST shows a better one when there is one — the owner's rename, a title the caller
        passed, the heading of the task file the first message named (:func:`apply_title`, all of
        them words someone wrote). This is the fallback under them, and it stays derived —
        taken from the FIRST user message rather than the last:
        a conversation is findable by how it started ("fix the login redirect"), not by whatever it
        drifted to. Nothing is summarised by a model — a title that costs a model call is a title
        that is sometimes missing, and one that paraphrases is a title that can be wrong.
        """
        return _title_from(self.messages)

    def to_dict(self) -> dict[str, Any]:

        return {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "selected_model": self.selected_model,

            "messages": [_as_dict(m) for m in self.messages],
            "receipts": self.receipts[-MAX_RECEIPTS:],
            # Only once stamped: a file with no stamp reads exactly as it did before the field.
            **({"checkout": self.checkout.to_dict()} if self.checkout is not None else {}),
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



def _meta_from(data: dict[str, Any], stem: str, mtime: float) -> dict[str, Any]:
    """The list's row for one stored conversation: what `CodeSessionStore.list_meta` shows."""
    messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
    # The last stored receipt's verdict: the one fact about how the last turn ended that a
    # conversation older than the marks file (`code_session_marks`) carries on its own.
    receipts = data.get("receipts")
    last = receipts[-1] if isinstance(receipts, list) and receipts else None
    verified = last.get("verified") if isinstance(last, dict) else None
    last_verdict = str(verified.get("state") or "") if isinstance(verified, dict) else ""
    return {
        "id": str(data.get("session_id") or stem),
        "title": _title_from(messages),
        "title_source": "message",
        "workspace": str(data.get("workspace") or ""),
        # Turns, not messages: a user asking twice is two turns, but the transcript between them
        # holds every tool call the agent made, and counting those would report a number that grows
        # with the agent's verbosity rather than with the conversation.
        "turns": sum(1 for m in messages if _is_person_turn(m)),
        "updated_at": mtime,

        "last_verdict": last_verdict,
        **_checkout_fields(Checkout.from_dict(data.get("checkout"))),
    }


def apply_title(row: dict[str, Any], mark: ConversationMark | None) -> dict[str, Any]:
    """Put the title the list shows on ``row``, from ``mark`` over the row's derived one, and say
    where it came from (``title_source``). The precedence is in :func:`choose_title`."""
    derived = str(row.get("title") or "")
    if mark is None:
        row["title"], row["title_source"] = derived, "message"
        return row
    row["title"], row["title_source"] = choose_title(
        owner=mark.title,
        stamped=mark.stamped_title,
        stamped_source=mark.stamped_title_source,
        derived=derived,
    )
    return row


def _checkout_fields(found: Checkout | None) -> dict[str, Any]:
    """The list row's view of a checkout: what the sidebar groups and labels by."""
    if found is None:
        return {"project": "", "project_name": "", "branch": None, "checkout_kind": None}
    return {
        "project": found.project_root,
        "project_name": found.name,
        "branch": found.branch,
        "checkout_kind": found.kind,
    }


def _backup_of(path: Path) -> Path:
    """Where the previous good copy of a conversation file is kept: beside it, as ``<id>.json.bak``.

    Not ``*.json``, so the list never reads it as a conversation of its own.
    """
    return path.with_name(f"{path.name}.bak")


def _damaged_copy_of(path: Path) -> Path:
    """Where an unreadable conversation file is kept before a save would write over it."""
    return path.with_name(f"{path.name}.damaged")


def _place_beside(path: Path, target: Path) -> None:
    """Make ``target`` hold what ``path`` holds now, atomically: a hard link staged beside it, or a
    copy where links are refused (FAT, some network shares), then put in place by a rename."""
    import shutil

    staged = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        try:
            os.link(path, staged)
        except OSError:
            shutil.copyfile(path, staged)
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


def _parse_document(path: Path) -> dict[str, Any]:
    """The JSON object stored at ``path``; raises ``OSError``/``ValueError`` when there is none."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def _read_with_backup(path: Path) -> tuple[dict[str, Any] | None, Exception | None]:
    """The stored conversation, from its file or — when that does not parse — from its backup.

    ``(document, None)`` when either was readable; ``(None, error)`` with the FILE's error when
    neither was. Observed 2026-10-18 (study 34, item 9): four conversations from 16/10 whose JSON
    did not parse were left out of the list with a log line, and to the owner they had vanished.
    """
    try:
        return _parse_document(path), None
    except (OSError, ValueError) as exc:
        first = exc
    backup = _backup_of(path)
    if backup.is_file():
        try:
            document = _parse_document(backup)
        except (OSError, ValueError):
            return None, first
        _log.warning(
            "code session %s unreadable (%s); read from its previous copy", path.stem, first
        )
        return document, None
    return None, first


def _damaged_row(path: Path, mtime: float, error: Exception) -> dict[str, Any]:
    """The list row for a conversation neither whose file nor whose backup parses.

    Listed, never hidden and never deleted: the file stays where it is, and the row says what it
    is. The title is built from what the file system still knows — its id and its date — because
    the title the conversation had lives in the file that does not parse.
    """
    from datetime import datetime

    when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    return {
        "id": path.stem,
        "title": f"Damaged conversation ({when}, {path.stem[:8]}): the file could not be read",
        "title_source": "message",
        "workspace": "",
        "turns": 0,
        "updated_at": mtime,
        "last_verdict": "",
        "damaged": True,
        "damage": f"{type(error).__name__}: {str(error)[:200]}",
        **_checkout_fields(None),
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
        # Beside the folder, not in it: `list_meta` reads every `*.json` in the folder as a
        # conversation. What the list derives a conversation's state from, and its archive.
        self.marks = CodeSessionMarks(self.root.with_name(f"{self.root.name}.marks.json"))
        # Serialises this store's writes of one file against each other: a save that moves an old
        # transcript's PR record into the marks file reads the stored record and then writes the
        # transcript without it, and two of those interleaved could drop the record.
        self._write_lock = threading.RLock()
        # What `list_meta` derived from each file, keyed by the file's `(mtime_ns, size)` when it
        # was read. Only the list's row is kept, never the conversation. A file that
        # did not parse at that version, nor its backup, is cached as its damaged row
        # (`_damaged_row`), so it is warned about once rather than on every list.
        self._meta_cache: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}
        self._meta_lock = threading.Lock()
        # The LEGACY PR record of each file (C1 wrote one into the transcript; the marks file holds
        # them now), keyed the same way and under the same lock. Filled from the same parse as the
        # row, so the list can merge an old conversation's PR without opening its file again, and
        # `save` can learn whether there is one to move without reading the file back.
        self._pull_request_cache: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}
        # The owner's model pin, cached with the same file version so a transcript save can preserve
        # a concurrent settings-card decision without reopening an unchanged transcript.
        self._model_cache: dict[str, tuple[tuple[int, int], str]] = {}
        # What each workspace string of an UNSTAMPED conversation is a checkout of, for the life of
        # the process: a few dozen strings, each answered by reading two or three small files once.
        # A folder that could not be read is not remembered, so it is asked again next time.
        self._derived_checkouts: dict[str, Checkout] = {}

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
            if not session.pin_changed and path.exists():
                # A settings card can update the pin while a turn is running. Its final transcript
                # save must preserve that newer owner decision rather than its stale in-memory pin.
                # Listing or writing the transcript already cached the pin for this exact version;
                # use it instead of rereading the unchanged file on this hot save path.
                try:
                    st = path.stat()
                    version = (st.st_mtime_ns, st.st_size)
                except OSError:
                    version = (-1, -1)
                with self._meta_lock:
                    cached_model = self._model_cache.get(str(path))
                existing_model = (
                    cached_model[1]
                    if cached_model is not None and cached_model[0] == version
                    else None
                )
                if existing_model is None:
                    existing = self._read_document(path)
                    existing_model = (
                        str(existing.get("selected_model") or "") if existing is not None else None
                    )
                if existing_model is not None:
                    document["selected_model"] = existing_model
                    session.selected_model = existing_model
            # A transcript written before the marks file held PRs still carries C1's record, and
            # this write is the one that would drop it: move it there first. If that fails the
            # record stays in the transcript, where the next save tries again.
            legacy = self._stored_pull_request(path)
            if legacy is not None:
                try:
                    self.marks.adopt_pull_request(session.session_id, legacy)
                except (OSError, ValueError) as exc:
                    _log.warning("pull request of %s not moved: %s", session.session_id, exc)
                    document["pull_request"] = legacy
            self._write(path, redact_json(document))

    def _write(self, path: Path, document: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: written beside the file, then put in its place. A reader that met half a file used
        # to read it as unreadable, and `load` starts an unreadable conversation fresh, so a list or a
        # next turn landing mid-save could see the conversation vanish or restart.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            payload = json.dumps(document)
            tmp.write_text(payload, encoding="utf-8")
            self._keep_previous(path)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        # The list's row for what was just written, from the same text, so the next list does not
        # read this file back. Parsed rather than taken from the dict so the row is exactly what a
        # read of the file would give.
        try:
            st = path.stat()
            meta = _meta_from(json.loads(payload), path.stem, st.st_mtime)
        except (OSError, ValueError):
            return
        saved_document = json.loads(payload)
        record = (
            pull_request_record(saved_document.get("pull_request"))
            if isinstance(saved_document, dict)
            else None
        )
        with self._meta_lock:
            version = (st.st_mtime_ns, st.st_size)
            self._meta_cache[str(path)] = (version, meta)
            self._pull_request_cache[str(path)] = (version, record)
            self._model_cache[str(path)] = (
                version,
                str(saved_document.get("selected_model") or "")
                if isinstance(saved_document, dict)
                else "",
            )

    def _keep_previous(self, path: Path) -> None:
        """Before ``path`` is replaced: its current version becomes the backup when it parses, and is
        set aside as ``.damaged`` when it does not — so a save never writes over the only copy.

        The write itself was already atomic, so a torn file is not what this is for: it is for a
        file damaged by anything else (a crash of an older build, a sync tool, a disk), which used
        to be read as unreadable, listed as nothing, and overwritten by the conversation's next turn.
        A failure here is logged and the save goes on: losing the backup must not lose the turn.

        Kept by a hard link where the file system allows one, so the common save neither reads nor
        copies the old file: the list's cache already parsed that exact version (the cache exists
        because re-reading conversations stalled the server), and the replace that follows leaves
        the old content alive under the backup's name only. A copy is the fallback.
        """
        if not path.is_file():
            return
        try:
            st = path.stat()
            with self._meta_lock:
                cached = self._meta_cache.get(str(path))
            version = (st.st_mtime_ns, st.st_size)
            row = cached[1] if cached is not None and cached[0] == version else None
            if row is not None:
                good = not row.get("damaged")
            else:
                try:
                    good = isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
                except ValueError:
                    good = False
            if good:
                _place_beside(path, _backup_of(path))
                return
            damaged = _damaged_copy_of(path)
            if not damaged.exists():
                _place_beside(path, damaged)
                _log.warning(
                    "code session %s was unreadable before this save; kept as %s",
                    path.stem, damaged.name,
                )
        except OSError as exc:
            _log.warning("code session %s: previous copy not kept: %s", path.stem, exc)

    @staticmethod
    def _read_document(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        data, _error = _read_with_backup(path)
        return data

    def _stored_pull_request(self, path: Path) -> dict[str, Any] | None:
        try:
            st = path.stat()
        except OSError:
            return None  # no file, so no record — and nothing to read
        version = (st.st_mtime_ns, st.st_size)
        with self._meta_lock:
            cached = self._pull_request_cache.get(str(path))
        if cached is not None and cached[0] == version:
            return dict(cached[1]) if cached[1] is not None else None
        data = self._read_document(path)
        record = pull_request_record(data.get("pull_request")) if data is not None else None
        with self._meta_lock:
            self._pull_request_cache[str(path)] = (version, record)
        return dict(record) if record is not None else None

    # ---- the pull requests of a conversation: ONE record, in the marks file ----------------------
    # The agent's `open_pull_request` and the owner's Git panel both write through
    # `record_pull_request`; the status card, the sidebar's count and the list's state all read the
    # same list. A transcript from before (C1) may still carry one record of its own: it is merged in
    # on every read and moved to the marks file by the first write that touches either.

    def pull_requests(self, session_id: str) -> list[dict[str, Any]]:
        """Every PR opened from a conversation, oldest first, with each one's options."""
        return merge_pull_requests(
            self.marks.get(session_id).pull_requests,
            self._stored_pull_request(self._path(session_id)),
        )

    def pull_request_of(self, session_id: str) -> dict[str, Any] | None:
        """The latest PR of a conversation — the one the status card shows — or None."""
        recorded = self.pull_requests(session_id)
        return dict(recorded[-1]) if recorded else None

    def listed_pull_requests(
        self, marks: dict[str, ConversationMark]
    ) -> dict[str, list[dict[str, Any]]]:
        """Each listed conversation's PRs, for the rows :meth:`list_meta` just produced.

        Opens no conversation file: the marks are the ones the list already read, and an old
        transcript's record comes from the cache filled by the same parse as its row. Call it after
        ``list_meta``, which is what brings that cache up to date.
        """
        with self._meta_lock:
            legacy = {
                Path(key).stem: record
                for key, (_version, record) in self._pull_request_cache.items()
                if record is not None
            }
        out: dict[str, list[dict[str, Any]]] = {}
        for sid in {*marks, *legacy}:
            recorded = marks[sid].pull_requests if sid in marks else []
            merged = merge_pull_requests(recorded, legacy.get(sid))
            if merged:
                out[sid] = merged
        return out

    def record_pull_request(
        self, session_id: str, *, number: int, url: str, require_stored: bool = True
    ) -> bool:
        """Record a PR opened from a conversation, by the owner's panel or the agent's tool; False
        when there is no such conversation or the number is not one.

        The owner's panel names a conversation from the client, so by default only an EXISTING one
        is written — a record for an id with no file would be a PR on a conversation nobody has. The
        agent's tool passes ``require_stored=False``: its conversation is the turn's own, found by
        the server, and a first turn has not saved its transcript yet when it opens the PR. Either
        way no transcript is created. The same PR again keeps the owner's options; a new one starts
        with both off, because options given for one PR are not consent for the next.
        """
        if pull_request_record({"number": number, "url": url}) is None:
            return False
        path = self._path(session_id)
        if require_stored and not path.is_file():
            return False
        with self._write_lock:
            self.marks.pull_request_opened(
                session_id, number=number, url=url, legacy=self._stored_pull_request(path)
            )
        return True

    def set_pull_request_options(
        self, session_id: str, options: dict[str, bool]
    ) -> dict[str, Any] | None:
        """Change the owner's options on a conversation's latest PR; None when it has none.

        Unknown keys are ignored rather than stored, so this cannot become a way to write arbitrary
        fields into the marks file.
        """
        path = self._path(session_id)
        if not path.is_file():
            return None
        with self._write_lock:
            return self.marks.set_pull_request_options(
                session_id, options, legacy=self._stored_pull_request(path)
            )

    def load_existing(self, session_id: str, agent: SupportsCodeRun) -> CodeSession | None:
        """The stored session, or None when nothing readable is stored under this id.

        Unlike `load`, which answers a fresh conversation for a missing or unreadable file (right
        for a first turn), this is for bringing an existing session object up to date, where a fresh
        one would silently replace a real conversation with an empty one.
        """
        if not self._path(session_id).is_file():
            return None
        stored = self.load(session_id, agent)
        # A conversation can have been explicitly pinned before its first transcript was saved
        # (for example, a turn that failed before the provider returned). That choice is durable
        # session state too; treating the file as nonexistent would let a changed global default
        # overwrite it on the next turn. The same holds for a PR recorded on it.
        has = (
            stored.messages
            or stored.receipts
            or stored.workspace
            or stored.pull_request
            or stored.selected_model
        )
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
        session.bypass_permissions = session_bypass(session.session_id)
        if not session.workspace:
            session.workspace = stored.workspace
        if session.checkout is None:
            session.checkout = stored.checkout
        if not session.pin_changed:
            session.selected_model = stored.selected_model

    def load(self, session_id: str, agent: SupportsCodeRun) -> CodeSession:
        """Return the stored session, or a fresh one under that id if there is nothing stored.

        A missing file is the ordinary first-turn case, not an error. A *corrupt* file is treated
        the same way and logged: refusing to start a conversation because an old one is unreadable
        would turn a bad write into a permanently broken session.
        """
        path = self._path(session_id)
        if not path.is_file():
            return CodeSession(agent, session_id=session_id)
        data, error = _read_with_backup(path)
        if data is None:
            # Fresh in memory only: the damaged file stays on disk, and the save that follows sets
            # it aside as `.damaged` before writing (`_keep_previous`) instead of over it.
            _log.warning("code session %s unreadable, starting fresh: %s", session_id, error)
            return CodeSession(agent, session_id=session_id)
        messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
        return CodeSession(
            agent,
            session_id=session_id,
            workspace=str(data.get("workspace") or ""),
            selected_model=str(data.get("selected_model") or ""),
            bypass_permissions=False,
            messages=list(messages),
            # Absent in every file written before receipts existed, and that is the ordinary case
            # rather than a defect: an old conversation has no accounting to show, and saying so by
            # showing none is right.
            receipts=[r for r in data.get("receipts", []) if isinstance(r, dict)],
            # The marks file's latest PR, with an old transcript's own C1 record merged in.
            pull_request=self._latest_pull_request(session_id, data.get("pull_request")),
            checkout=Checkout.from_dict(data.get("checkout")),
        )

    def _latest_pull_request(self, session_id: str, legacy: object) -> dict[str, Any] | None:
        recorded = merge_pull_requests(
            self.marks.get(session_id).pull_requests, pull_request_record(legacy)
        )
        return dict(recorded[-1]) if recorded else None

    def stored_model(self, session_id: str) -> str:
        """The model a stored conversation is pinned to, or "" (none pinned, nothing stored, or an
        unreadable file — all of which mean "follow the install default")."""
        return str(self._stored_field(session_id, "selected_model"))

    def set_stored_model(self, session_id: str, model: str) -> None:
        """Set/clear a conversation's model pin without replacing its transcript."""
        path = self._path(session_id)
        with self._write_lock:
            existed = path.exists()
            document = self._read_document(path)
            if existed and document is None:
                raise ValueError("the conversation file is unreadable; its model was not changed")
            document = document or {}
            document["selected_model"] = model
            self._write(path, redact_json(document))

    def stored_bypass_permissions(self, session_id: str) -> bool:
        """The process-local screen preference; legacy JSON flags are deliberately ignored."""
        return session_bypass(session_id)

    def stored_workspace(self, session_id: str) -> str:
        """The folder a stored conversation belongs to, or "" (none stored, unreadable, or the
        server's own folder — "" is the request's convention for "yours")."""
        return str(self._stored_field(session_id, "workspace"))

    def _stored_field(self, session_id: str, name: str) -> object:
        try:
            path = self._path(session_id)
        except ValueError:
            return ""
        data = self._read_document(path) or {}
        if name == "bypass_permissions":
            return False
        return data.get(name) or ""

    def list_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))

    def list_meta(
        self, marks: dict[str, ConversationMark] | None = None
    ) -> list[dict[str, Any]]:
        """Every stored conversation, newest first, with just enough to draw a list.

        The files are the only source of truth; what is kept in memory is a cache of each file's
        row, keyed by the file's `(mtime_ns, size)`, and a file is read again only when that changes.
        Reading every file on every call was measured to stall the desktop server: the sidebar asks
        for this list each time the set of running turns changes, so with ~15 turns running it was
        asked several times a second, and each call parsed all 69 conversations (some 190 KB+) under
        the GIL until the supervisor killed the server and every turn with it.

        An unreadable file never makes the whole list refuse to render, which would look like "you
        have no conversations". It is read from its previous copy when that parses, and otherwise
        listed as a damaged row (`damaged: true`) — never left out, never deleted. It is warned
        about once per version of the file, not once per call.
        """
        if not self.root.is_dir():
            return []
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in self.root.glob("*.json"):
            try:
                st = path.stat()
            except OSError:
                continue  # gone between the listing and the look
            key_path = str(path)
            seen.add(key_path)
            version = (st.st_mtime_ns, st.st_size)
            with self._meta_lock:
                cached = self._meta_cache.get(key_path)
            if cached is not None and cached[0] == version:
                meta = cached[1]
            else:
                meta, legacy = self._read_meta(path, st.st_mtime)
                with self._meta_lock:
                    self._meta_cache[key_path] = (version, meta)
                    self._pull_request_cache[key_path] = (version, legacy)
            if meta is not None:
                # A copy: callers decorate the rows (`running`, `state`), and that must not leak
                # into the cache and from there into the next call's answer.
                out.append(dict(meta))
        # The titles are applied on every list, over a copy of each cached row: the marks file is
        # read here rather than cached (the owner can rename from the app while the CLI lists), and
        # a rename writes no transcript, so a cache keyed by the transcript's version would miss it.
        # ``marks`` is the caller's own read when it has one, so a list that also derives state from
        # the marks opens that file once, not twice.
        if marks is None:
            marks = self.marks.all()
        for row in out:
            apply_title(row, marks.get(str(row["id"])))
        with self._meta_lock:
            for gone in [k for k in self._meta_cache if k not in seen]:
                del self._meta_cache[gone]
            for gone in [k for k in self._pull_request_cache if k not in seen]:
                del self._pull_request_cache[gone]
            for gone in [k for k in self._model_cache if k not in seen]:
                del self._model_cache[gone]
        out.sort(key=lambda m: float(m["updated_at"]), reverse=True)
        return out

    def _read_meta(
        self, path: Path, mtime: float
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """The file's row and its legacy PR record, from one parse."""
        data, error = _read_with_backup(path)
        if data is None:
            # Listed as damaged rather than left out (study 34, item 9): a conversation that
            # disappears from the list reads as deleted, and nothing here deleted it.
            _log.warning("code session %s unreadable, listed as damaged: %s", path.stem, error)
            return _damaged_row(path, mtime, error or ValueError("unreadable")), None
        meta = _meta_from(data, path.stem, mtime)
        try:
            st = path.stat()
        except OSError:
            pass
        else:
            with self._meta_lock:
                self._model_cache[str(path)] = (
                    (st.st_mtime_ns, st.st_size), str(data.get("selected_model") or "")
                )
        if not meta["project"] and meta["workspace"]:
            meta.update(_checkout_fields(self._derived_checkout(str(meta["workspace"]))))
        return meta, pull_request_record(data.get("pull_request"))

    def _derived_checkout(self, workspace: str) -> Checkout | None:
        """What an unstamped conversation's folder is a checkout of, read now if it still exists.

        Conversations from before the stamp would otherwise each stay a group of their own until
        their next turn. A folder already gone answers None, and the row falls back to its
        workspace string — which is exactly how it was listed before.
        """
        with self._meta_lock:
            known = self._derived_checkouts.get(workspace)
        if known is not None:
            return known
        found = checkout_of(workspace)
        if found is not None:
            with self._meta_lock:
                self._derived_checkouts[workspace] = found
        return found

    def exists(self, session_id: str) -> bool:
        """Whether a conversation file is stored for this id (an unusable id is not stored)."""
        try:
            return self._path(session_id).is_file()
        except ValueError:
            return False

    def delete(self, session_id: str) -> bool:
        # Its "Ignore permissions" goes with it (M1), before the file check: a conversation that was
        # never saved can still hold a switch, and it must not outlive the conversation it named.
        forget_session_bypass(session_id)
        path = self._path(session_id)
        if not path.is_file():
            return False
        path.unlink()
        # The owner deleted the conversation: its previous copy and any damaged copy go with it.
        for kept in (_backup_of(path), _damaged_copy_of(path)):
            kept.unlink(missing_ok=True)
        with self._meta_lock:
            self._meta_cache.pop(str(path), None)
            self._pull_request_cache.pop(str(path), None)
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
        data, error = _read_with_backup(source)
        if data is None:
            _log.warning("code session %s unreadable, not forked: %s", session_id, error)
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
        # Its name goes with it: a fork of "Tabs and panel split" is still that task, and without
        # this it would fall back to the first message — the very line that made every row alike.
        try:
            self.marks.copy_titles(session_id, new_id)
        except (OSError, ValueError) as exc:
            _log.warning("title of %s not copied to its fork: %s", session_id, exc)
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
