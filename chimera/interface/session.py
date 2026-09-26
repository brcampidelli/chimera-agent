"""The conversational session core.

``ChatSession`` turns the single-shot agent into a multi-turn assistant: it keeps
a rolling transcript, optionally recalls relevant long-term memory, and composes
both into each turn's prompt. It depends only on small protocols, so a fake agent
and memory make it fully testable without a network — and the real CLI ``chat``
command, the TUI, and the messaging gateway all reuse it unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from chimera.core.agent import AgentResult, ToolActivity
from chimera.core.code_session import _accepts, _as_dict
from chimera.memory.gate import MemoryGate
from chimera.memory.models import EVERY_PROJECT, MemoryItem
from chimera.providers.gateway import MessageLike
from chimera.telemetry import get_logger

_log = get_logger("interface.session")

#: What is known about untrusted content in one stored turn.
#:
#: Three values and not two, because "nothing untrusted entered" and "nobody was in a position to
#: say" are different facts, and a store that collapses them publishes the more comforting of the
#: two. ``UNKNOWN`` is what every turn written before this field existed is, and it is treated the
#: way ``TAINTED`` is when the turn comes back from disk.
CLEAN = "clean"
TAINTED = "tainted"
UNKNOWN = "unknown"

_PROVENANCE = (CLEAN, TAINTED, UNKNOWN)


class SupportsRun(Protocol):
    """The agent loop: turn a task into a result with a final answer.

    ``on_token``/``on_tool`` are optional live callbacks (streaming + tool activity); a backend that
    ignores them still satisfies this — ``send()`` never passes them, only ``send_verbose()`` does.
    """

    def run(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = ...,
        on_tool: Callable[[ToolActivity], None] | None = ...,
    ) -> AgentResult: ...


class SupportsHistoryRun(Protocol):
    """An agent loop that can also take earlier turns as messages and this turn's notes.

    Separate from :class:`SupportsRun` because that protocol is published and an implementation
    written against it is valid without either keyword. :meth:`ChatSession._real_history_ready`
    reads the signature before it relies on this one.
    """

    def run(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = ...,
        on_tool: Callable[[ToolActivity], None] | None = ...,
        history: list[MessageLike] | None = ...,
        turn_notes: str | None = ...,
    ) -> AgentResult: ...


class SupportsRecall(Protocol):
    """Long-term memory: keyword recall over stored facts."""

    def search(self, query: str, *, k: int = 5) -> list[MemoryItem]: ...


class SupportsRelated(Protocol):
    """Graph memory: recall facts linked to entities mentioned in the query."""

    def related_facts(self, query: str, k: int = 5) -> list[str]: ...


@dataclass
class ChatTurn:
    """One exchange in the conversation, and what is known about where it came from.

    Two fields, deliberately on different axes.

    ``provenance`` belongs to the turn and travels with the file: ``CLEAN`` when every tool call
    this turn made was observed and none of them returned external content, ``TAINTED`` when one
    did, ``UNKNOWN`` when nobody could say. It is stamped once, when the turn is recorded, because
    that is the only moment the evidence exists — the run's taint ledger does not survive the
    process, and a reader on Thursday has nothing left to consult about Monday.

    ``restored`` belongs to THIS session's view and is not persisted: it says the turn came off
    disk rather than out of the model a moment ago. The distinction is what :meth:`ChatSession.
    _assemble` renders, and it is why the marker is not written into ``assistant``: that text is
    replayed into every later prompt, so a marker inside it would put words in the model's mouth
    for the rest of the conversation and be saved back that way, one layer deeper on each reopen.
    """

    user: str
    assistant: str
    provenance: str = UNKNOWN
    restored: bool = False
    #: The model's own messages for this turn, from the user's words to the reply, tool calls
    #: included, when the turn ran in this process under :attr:`ChatSession.real_history`.
    #:
    #: Not persisted, and outside equality, like ``restored``: it is this process's view of the turn.
    #: The session file keeps the prose pair it has always kept, so a file written in either mode
    #: loads in both, and a turn that comes back from disk is replayed as a user/assistant pair.
    #: Keeping tool results on disk would also keep whatever a fetched page said, beyond the run
    #: that fetched it, which is a separate decision from this one.
    messages: list[dict[str, Any]] | None = field(default=None, repr=False, compare=False)


def _as_messages(turn: ChatTurn) -> list[dict[str, Any]]:
    """A turn with no recorded messages, as the user/assistant pair a model would have produced.

    The trust treatment is :func:`_replay`'s, carried into the assistant message because a message
    list has no role label to put it on. A restored turn opens with its label, and one not known to
    be clean has its reply inside the data fence: the model did not just say it, and the run that
    could vouch for it is over. The label goes into the text sent, never into ``turn.assistant``,
    which is what the session file keeps.
    """
    reply = turn.assistant
    if turn.restored:
        from chimera.governance.ledger_tool import fence

        label = _RESTORED.get(turn.provenance, _RESTORED[UNKNOWN]).strip()
        body = turn.assistant if turn.provenance == CLEAN else fence(turn.assistant)
        reply = f"{label}\n{body}"
    return [{"role": "user", "content": turn.user}, {"role": "assistant", "content": reply}]


def _turn_messages(result: AgentResult, message: str) -> list[dict[str, Any]] | None:
    """This turn's part of the run's transcript, or None when it cannot be found intact.

    It starts at the last user message that is the person's own words, which is where the turn
    began: a nudge the loop adds mid-turn says something else, and every earlier turn is before it.
    It must end on the reply the person was shown. When either end is missing (a compaction folded
    the turn's message into a summary, or the run ended without an assistant message), the turn is
    kept as its prose pair instead: a shorter history is a degradation, a history the model never
    saw is a fabrication.
    """
    body = [_as_dict(m) for m in result.transcript]
    body = [m for m in body if m.get("role") != "system"]
    for start in range(len(body) - 1, -1, -1):
        if body[start].get("role") == "user" and body[start].get("content") == message:
            segment = body[start:]
            last = segment[-1]
            if last.get("role") == "assistant" and last.get("content") == result.answer:
                return segment
            return None
    return None


@dataclass(frozen=True)
class DeclinedTool:
    """A tool call that did NOT do what it was asked: a gate refused it, or it errored.

    ``reason`` is the observation the tool itself returned, verbatim (whitespace-normalised and
    capped) — not a summary written here. The model already saw that string and is free to
    paraphrase it into "the command printed exactly: marker-42"; the person needs the original
    beside the reply to see that nothing ran.
    """

    name: str
    reason: str


def decline_reason(observation: str) -> str:
    """The tool's own words, on one line, short enough to sit under a reply."""
    text = " ".join(observation.split())
    return text[:200] + "…" if len(text) > 200 else text


def read_provenance(value: Any) -> str:
    """The stored label, or ``UNKNOWN`` for anything this version does not recognise.

    A missing field is an old file; an unrecognised one is a newer file, a hand edit, or a hostile
    write. None of the three is evidence that nothing untrusted entered, so all three read the same.
    """
    text = str(value)
    return text if text in _PROVENANCE else UNKNOWN


def turn_provenance(
    reported: list[str], observed: list[ToolActivity] | None, *, already_tainted: bool
) -> str:
    """What is known about untrusted content in a finished turn.

    ``reported`` is ``AgentResult.tool_names`` — every tool the loop actually called. ``observed``
    is the live ``ToolActivity`` stream when the caller subscribed to one (``send_verbose``), or
    ``None`` when it did not (``send``).

    Three rules, in order:

    * ``already_tainted`` wins. Taint is monotonic within a thread, exactly as
      :meth:`TaintLedger.run_tainted` is within a run: the fetched text is still in the prompt six
      turns later, so every answer after it is downstream of it.
    * a **fetch tool** by name is untrusted content by definition, and this is the half that works
      on a surface with no taint ledger at all — which is every terminal surface today.
    * an observation carrying the ``<<external-data>>`` fence is untrusted content that a
      ``LedgeredTool`` already recognised. This is the accurate half, and it only exists where the
      governed registry is built.

    The gap, stated rather than hidden: an MCP or OpenAPI tool marked ``untrusted_output`` has a
    name that is not in ``FETCH_TOOLS`` (it comes from a remote server), so on a surface with no
    ledger to fence it this returns ``CLEAN`` for a turn that read external content. ``ToolActivity``
    carries the name and the observation, not the tool object, so the marker cannot be consulted
    from here. Closing it means giving that surface a ledger.
    """
    if already_tainted:
        return TAINTED
    from chimera.governance.ledger import FETCH_TOOLS

    if any(name in FETCH_TOOLS for name in reported):
        return TAINTED
    if observed is None:
        # Nothing was watched. With no tool call at all there is nothing to have watched, so this
        # is `CLEAN` honestly; with one, it is a measurement that was not taken.
        return CLEAN if not reported else UNKNOWN
    if len(observed) != len(reported):
        # The agent ran tools it did not announce. `CLEAN` here would be a guarantee derived from
        # an instrument that could not have shown the opposite.
        return UNKNOWN
    from chimera.governance.ledger_tool import FENCE_OPEN

    if any(FENCE_OPEN in activity.observation for activity in observed):
        return TAINTED
    return CLEAN


#: How a replayed turn is labelled, by what is known about it. The label sits on the ROLE, never
#: inside the reply — see :class:`ChatTurn`.
_RESTORED = {
    CLEAN: " [restored from the saved transcript]",
    UNKNOWN: " [restored from the saved transcript; provenance was not recorded]",
    TAINTED: " [restored from the saved transcript; untrusted content had entered this conversation]",
}


def recent_turns(turns: list[ChatTurn], size: int) -> list[ChatTurn]:
    """The last ``size`` turns: the window a prompt replays.

    ``turns[-size:]`` is the obvious spelling and it is wrong at 0: ``turns[-0:]`` is the whole list,
    so a session asked to replay no history replayed all of it — every turn, growing without bound.
    """
    return turns[-size:] if size > 0 else []


def _replay(turns: list[ChatTurn]) -> str:
    """Render the recent turns for the prompt, saying which of them the model did not just say.

    A restored turn is fenced unless it is known to be clean. Within one process the surface shows
    the person each turn as it happens — its refusals, its tools, its price — and, once the taint
    ledger reaches this surface, narrows the dangerous tools for the rest of the run. Across a
    restart none of that survives: the ledger is gone, nobody watched, and the reply is replayed
    into a fresh prompt as if the model had just written it. That gap is the one this closes; a
    tainted turn replayed inside the SAME run is a real and adjacent risk that the ledger's
    narrowing covers and this does not.
    """
    from chimera.governance.ledger_tool import fence

    lines = ["Conversation so far:"]
    for turn in turns:
        lines.append(f"User: {turn.user}")
        if not turn.restored:
            lines.append(f"Assistant: {turn.assistant}")
            continue
        label = _RESTORED.get(turn.provenance, _RESTORED[UNKNOWN])
        if turn.provenance == CLEAN:
            lines.append(f"Assistant{label}: {turn.assistant}")
        else:
            # `fence()` and not an f-string: the close marker is a public constant in an open-source
            # repo, so a reply that quotes it would end the fence early and let its tail read as if
            # it were outside. `fence` neutralises it; hand-assembly does not.
            lines.append(f"Assistant{label}:")
            lines.append(fence(turn.assistant))
    return "\n".join(lines)


@dataclass
class TurnReport:
    """A turn's answer plus the activity a UI can surface: tools, tokens, cost, memory recall.

    Everything here is derived from what the agent actually did this turn — no fabricated signals.
    ``usd`` is None when the model's price is unknown; ``memory_layer`` is None unless the (optional)
    which-layer instrumentation is wired, so the UI shows the honest count without guessing the layer.
    """

    answer: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float | None = None
    tool_names: list[str] = field(default_factory=list)
    memory_facts_used: int = 0
    memory_layer: str | None = None
    model: str = ""  # the model slug that answered this turn (for the usage log's per-model breakdown)
    steps: int = 0
    stopped_reason: str = ""
    # Per-turn fusion/cascade trace from the backend (UI-ready JSON), or None for a single-model turn.
    route_meta: dict[str, Any] | None = None
    # The fact this turn saved to durable memory (an explicit "remember that…"), or None. Lets a UI
    # confirm "remembered" honestly — set only when a fact was actually written.
    memory_saved: str | None = None
    #: Tool calls this turn that a gate refused or that errored, in the order they happened.
    #:
    #: Without this a refusal is invisible above the surface: `run_shell` hands back
    #: "error: host execution declined (CHIMERA_HOST_EXEC). Not run." as an ordinary observation,
    #: the model reads it and answers "The command printed exactly: marker-42", and nothing in the
    #: reply says the command never ran. `tool_names` cannot carry it — it records that a tool was
    #: called, which is the very thing that is true in both cases.
    declined: list[DeclinedTool] = field(default_factory=list)
    #: What this turn's own provenance was recorded as — see :func:`turn_provenance`.
    provenance: str = UNKNOWN
    #: The task list the agent last wrote this turn with ``todo_write``, as ``(task, status)`` pairs,
    #: or empty when it wrote none. The desktop draws this list live; the terminal registered the
    #: tool (on by default) and drew nothing, so the model kept a checklist nobody at a terminal saw.
    todos: list[tuple[str, str]] = field(default_factory=list)



def last_todo_list(observed: list[ToolActivity]) -> list[tuple[str, str]]:
    """The list the last ACCEPTED ``todo_write`` of a turn recorded, as ``(task, status)`` pairs.

    Read from the call's own arguments, which is what the tool stored: it replaces the whole list
    each time, so the last accepted call is the list. A refused or failed call recorded nothing and
    is skipped. ``items`` may arrive as its JSON text, which the tool itself accepts too.
    """
    import json

    for activity in reversed(observed):
        if activity.name != "todo_write" or not activity.ok:
            continue
        raw = activity.arguments.get("items")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return []
        if not isinstance(raw, list):
            return []
        out: list[tuple[str, str]] = []
        for entry in raw:
            if isinstance(entry, dict):
                task = str(entry.get("task") or "").strip()
                if task:
                    out.append((task, str(entry.get("status") or "").strip().lower()))
        return out
    return []

@dataclass
class ChatSession:
    """Multi-turn, memory-aware conversation over a tool-using agent."""

    agent: SupportsRun
    memory: SupportsRecall | None = None
    graph: SupportsRelated | None = None
    gate: MemoryGate | None = field(default_factory=MemoryGate)
    profile: str = ""  # persistent user-profile preamble (persona facts), applied every turn
    max_history: int = 6
    memory_k: int = 3
    # When True, an explicit "remember that…" in a message writes a durable fact (opt-in for privacy:
    # chatting should not silently persist unless the user asked for it). Off keeps the prior
    # behaviour where the desktop chat never wrote memory.
    remember_from_chat: bool = False
    #: The folder this conversation is open on, as :func:`chimera.memory.models.project_key` spells
    #: it — recall then sees that project's facts plus the ones that belong everywhere.
    #:
    #: Defaults to :data:`EVERY_PROJECT` (no narrowing) because most callers are not a folder: the
    #: messaging gateway, the OpenAI-compatible endpoint and the benchmarks are conversations with
    #: nothing open. The terminal surfaces take a ``--workspace`` and pass it, which is what the
    #: coding turn has always done and what `chat` said it did and did not.
    project: str | None = EVERY_PROJECT
    #: How many turns the transcript keeps, or ``None`` to keep all of them (the default).
    #:
    #: This used to be an unconditional 50 and it was in the wrong place. Only ``max_history`` turns
    #: ever reach a prompt, so bounding the RECORD buys nothing there — and `SessionManager.persist`
    #: writes this list, so from turn 51 every save rewrote the file without turn 1. A store whose
    #: command promises "saved as you go" must not quietly become a sliding window.
    #:
    #: The surface that does need a bound is the one with no file and no end: the messaging gateway
    #: holds a live session per chat for as long as the process runs, and sets this.
    max_turns: int | None = None
    #: Called with the user's message at the top of every turn, before a single tool runs.
    #:
    #: It exists for one thing and the name stays general anyway: a surface whose governance ledger
    #: is built once per *session* has no other moment at which the turn's own words are known.
    #: ``guard_chat_registry`` builds such a ledger and said so in its own docstring — *"the mode
    #: travels; the instruction cannot"* — which was true until the terminal showed otherwise, and
    #: is why ``CHIMERA_TAINT_AUTHORITY`` did nothing in the app: ``requester_of`` answers
    #: ``unknown`` for a ledger nobody told an instruction, and the narrowing treats ``unknown``
    #: exactly as it treats ``agent``.
    #:
    #: ``None`` by default, and the default has to stay byte-identical: this class serves the
    #: messaging gateway, ``/v1/chat/completions`` and every bench, none of which asked for a hook.
    on_turn_start: Callable[[str], None] | None = None
    #: Where this session's pending approval questions are announced, for a surface that can draw
    #: one. Held rather than called: it is a :class:`chimera.governance.approval.ApprovalAnnouncer`,
    #: built with the tool registry and bound to a screen a moment later.
    #:
    #: It lives here for the same reason ``on_turn_start`` does — a mismatch of lifetimes. The
    #: registry, and therefore the approver holding this announcer, is built once per SESSION; the
    #: stream that can render the question exists once per TURN. The session object is the only
    #: thing both halves can see, so it is where the two are introduced.
    #:
    #: ``None`` by default, and that default has to stay byte-identical: this class also serves the
    #: messaging gateway, ``/v1/chat/completions`` and every bench, none of which has a screen. An
    #: announcer with nothing bound announces to nobody, which is exactly what those want.
    approval_sink: Any = None
    #: Send the earlier turns as the model's own messages, and the profile and recalled facts in the
    #: turn context, instead of flattening all of it into one user message.
    #:
    #: The flattened form loses every earlier tool call, and nothing after the system message can be
    #: cached, because the block opens with text that changes every turn (study 25, §7 S2/S3). Here
    #: the window is the same ``max_history`` turns, each one starting at its user message, so the
    #: list is only ever cut where a turn begins.
    #:
    #: ``False`` by default, which keeps the flattened form byte for byte: it is what the Discord
    #: bot and the benches send today, and flipping it is decided by `bench/chat_history`, not
    #: here. On, it still flattens for an agent that cannot take ``history`` and ``turn_notes`` or
    #: has no turn context, because the facts would otherwise have nowhere to go.
    real_history: bool = False
    turns: list[ChatTurn] = field(default_factory=list)

    def _begin_turn(self, message: str) -> None:
        """Announce the turn, in the one place both entry points can share.

        Called from ``send`` AND ``send_verbose``. Not a stylistic preference — the comment in
        ``send`` records that ``remember_from_chat`` once meant two different things depending on
        which of the two you called, so a hook added to only one of them would be that same bug with
        a different field name.
        """
        if self.on_turn_start is not None:
            self.on_turn_start(message)

    def send(self, message: str) -> str:
        """Run one user message through the agent and record the exchange."""
        self._begin_turn(message)
        messages: list[dict[str, Any]] | None = None
        if self._real_history_ready():
            facts, _layer = self._recall(message)
            result = self._run_with_history(message, facts)
            messages = _turn_messages(result, message)
        else:
            result = self.agent.run(self._compose(message))
        self._record(
            message,
            result.answer,
            turn_provenance(
                list(result.tool_names), None, already_tainted=self._thread_tainted()
            ),
        )
        self._keep_messages(message, messages)
        # `remember_from_chat` used to mean two different things depending on which method you
        # called: `send_verbose` honoured it and `send` did not. So every surface built on `send`
        # answered "Got it, I'll remember" with the flag ON and wrote nothing — a setting that is
        # true in the config and false in the product.
        self._maybe_remember(message)
        return result.answer

    def send_verbose(
        self,
        message: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> TurnReport:
        """Like :meth:`send`, but returns a :class:`TurnReport` (answer + tools/tokens/cost/memory)
        and forwards live ``on_token``/``on_tool`` callbacks to the agent. Recall runs once here and
        is reused for both the prompt and the report's fact count (no double search)."""
        self._begin_turn(message)
        facts, layer = self._recall(message)
        declined: list[DeclinedTool] = []
        observed: list[ToolActivity] = []

        def watch(activity: ToolActivity) -> None:
            """Collect the refusals on the way past, then hand the activity to the caller.

            Here rather than in each surface, because every surface needs the same answer and the
            agent only offers it live: `AgentResult` keeps the tool NAMES, which say a tool was
            called — true of a call that ran and of one a gate stopped. The same stream is what
            lets the turn be stamped `clean` rather than `unknown`, so it is kept whole and
            counted against `tool_names` afterwards.
            """
            observed.append(activity)
            if not activity.ok:
                declined.append(DeclinedTool(activity.name, decline_reason(activity.observation)))
            if on_tool is not None:
                on_tool(activity)

        messages: list[dict[str, Any]] | None = None
        if self._real_history_ready():
            result = self._run_with_history(message, facts, on_token=on_token, on_tool=watch)
            messages = _turn_messages(result, message)
        else:
            result = self.agent.run(
                self._assemble(message, facts), on_token=on_token, on_tool=watch
            )
        provenance = turn_provenance(
            list(result.tool_names), observed, already_tainted=self._thread_tainted()
        )
        self._record(message, result.answer, provenance)
        self._keep_messages(message, messages)
        saved = self._maybe_remember(message)
        return TurnReport(
            answer=result.answer,
            declined=declined,
            todos=last_todo_list(observed),
            memory_saved=saved,
            provenance=provenance,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
            usd=result.usd,
            tool_names=list(result.tool_names),
            memory_facts_used=len(facts),
            memory_layer=layer,
            model=result.model,
            steps=result.steps,
            stopped_reason=result.stopped_reason,
            route_meta=result.route_meta,
        )

    def _maybe_remember(self, message: str) -> str | None:
        """If enabled and the user explicitly asked to remember something, write it durably.

        Conservative by design: only an explicit "remember that…" instruction is captured (see
        :func:`chimera.memory.capture.parse_remember_request`) — never automatic extraction, which
        would pollute memory. Duck-typed on ``remember`` so a search-only memory backend is simply
        skipped; the write is deduped by the MemoryManager. Returns the fact saved, or None.

        Written with no project on purpose, even when the conversation has one. Recall narrows and
        must not hide: a fact the user asked for in one folder is theirs everywhere, and filing it
        under this folder would make it unreachable from the next one with nothing to say so.
        """
        if not self.remember_from_chat or self.memory is None:
            return None
        write = getattr(self.memory, "remember", None)
        if not callable(write):
            return None
        from chimera.memory.capture import parse_remember_request

        fact = parse_remember_request(message)
        if fact is None:
            return None
        write(fact, source="chat")  # deduped; clean provenance (the user asked for it directly)
        return fact

    def _thread_tainted(self) -> bool:
        """Has untrusted content already entered this conversation?

        Derived from the turns rather than kept in a flag, so a session hydrated from disk inherits
        the answer for free — which is the case that matters, since the ledger that knew it is gone.
        """
        return any(turn.provenance == TAINTED for turn in self.turns)

    def _record(self, message: str, answer: str, provenance: str = UNKNOWN) -> None:
        self.turns.append(ChatTurn(user=message, assistant=answer, provenance=provenance))
        if self.max_turns is not None and len(self.turns) > self.max_turns:
            del self.turns[: -self.max_turns]

    def _keep_messages(self, message: str, messages: list[dict[str, Any]] | None) -> None:
        """Attach the turn's own messages to the turn :meth:`_record` just wrote.

        A step of its own rather than a fourth argument to ``_record``, because ``_record`` is
        overridden (the scenario suite cuts the threading wire there) and a changed signature breaks
        every override in the flattened default too. A record that kept nothing gets nothing.
        """
        if messages is not None and self.turns and self.turns[-1].user == message:
            self.turns[-1].messages = messages

    def _real_history_ready(self) -> bool:
        """Whether this turn goes out as real history: the setting is on AND the agent can take it.

        Read from the agent rather than assumed, for the reason ``CodeSession._accepts`` gives:
        :class:`SupportsRun` is published, and an agent written against it has neither keyword. The
        turn context is checked too, because without it the notes are never read, and a turn that
        silently lost its recalled facts would be worse than one that flattened them.
        """
        if not self.real_history:
            return False
        run = self.agent.run
        config = getattr(self.agent, "config", None)
        ready = (
            _accepts(run, "history")
            and _accepts(run, "turn_notes")
            and bool(getattr(config, "turn_context", False))
        )
        if not ready:
            _log.debug("real history asked for, but this agent cannot take it; flattening")
        return ready

    def _history(self) -> list[MessageLike]:
        """The earlier turns inside the window, as messages, oldest first.

        Every turn contributes from its own user message, so the list always starts where a turn
        starts and a tool result is never separated from the call it answers. Copies, so a run that
        edits its message list cannot reach back into the record.
        """
        if self.max_history <= 0:
            return []
        out: list[MessageLike] = []
        for turn in self.turns[-self.max_history :]:
            out.extend(dict(m) for m in (turn.messages or _as_messages(turn)))
        return out

    def _turn_notes(self, facts: list[str]) -> str:
        """The profile and the recalled facts, for the turn context rather than the history.

        The same two things :meth:`_assemble` puts at the head of its block, in the same order. Here
        they head the turn's own message, which the loop gives back bare when the run ends, so the
        history never holds a copy that was true of an earlier question.
        """
        from chimera.prompts.context import facts_block

        return "\n\n".join(part for part in (self.profile, facts_block(facts)) if part)

    def _run_with_history(
        self,
        message: str,
        facts: list[str],
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> AgentResult:
        run = cast(SupportsHistoryRun, self.agent).run
        if on_token is None and on_tool is None:
            # `send` has never passed callbacks, and an agent that takes history is not thereby
            # promised to take them as well.
            return run(message, history=self._history(), turn_notes=self._turn_notes(facts))
        return run(
            message,
            on_token=on_token,
            on_tool=on_tool,
            history=self._history(),
            turn_notes=self._turn_notes(facts),
        )

    def reset(self) -> None:
        """Forget the conversation (long-term memory is untouched)."""
        self.turns.clear()

    def set_model(self, model: str | None) -> bool:
        """Switch the underlying agent's model mid-session (None = back to default).

        Returns True if the agent exposed a model setting to change.
        """
        config = getattr(self.agent, "config", None)
        if config is not None and hasattr(config, "model"):
            config.model = model
            return True
        return False

    def _recall(self, message: str) -> tuple[list[str], str | None]:
        """Recall long-term facts for this message. Delegates to :func:`recall_facts`.

        The logic lives at module level because a second surface needs it — the coding turn reads
        memory too — and the part that must never be reimplemented is the taint labelling. A copy
        that forgot it would let a poisoned memory re-enter a prompt looking clean.

        There used to be a local ``_memory_search`` passed in as ``search=``, which took the graded
        call away from ``recall_facts`` and reimplemented two thirds of it. The third it dropped was
        ``project``, so every terminal conversation recalled every folder's facts whatever
        ``--workspace`` said. Deleting the copy is the fix; there was nothing wrong with the original.
        """
        return recall_facts(
            message,
            memory=self.memory,
            graph=self.graph,
            gate=self.gate,
            k=self.memory_k,
            project=self.project,
        )

    def _compose(self, message: str) -> str:
        facts, _layer = self._recall(message)
        return self._assemble(message, facts)

    def _assemble(self, message: str, facts: list[str]) -> str:
        """Build the turn's prompt from the profile preamble, recalled facts, recent turns, message."""
        parts: list[str] = []
        if self.profile:  # persistent persona preamble — cross-session personalization
            parts.append(self.profile)
        if facts:
            parts.append("Relevant facts from memory:\n" + "\n".join(f"- {f}" for f in facts))
        window = recent_turns(self.turns, self.max_history)
        if window:
            parts.append(_replay(window))
        parts.append(f"User: {message}")
        return "\n\n".join(parts)


#: Sentinel for "the caller said nothing about a gate", which is not the same as "no gate".
#:
#: This parameter defaulted to ``None`` and the body only filtered ``if gate is not None``, so
#: forgetting the keyword silently disabled a trust boundary. The desktop coding turn was that
#: caller, on the path its own docstring calls "every conversation in the app": a memory carrying
#: override text arrived labelled as tainted and was not stopped. Fixing the call site alone would
#: leave the next caller one forgotten keyword from the same hole, which is how this one happened.
#: ``None`` still means "no gate" — the memory benchmark needs ungated recall to measure what the
#: gate costs — but it has to be typed now.
_GATE_UNSET: Any = object()


def recall_facts(
    message: str,
    *,
    memory: Any = None,
    graph: Any = None,
    gate: Any = _GATE_UNSET,
    k: int = 3,
    search: Any = None,
    project: str | None = EVERY_PROJECT,
) -> tuple[list[str], str | None]:
    """Long-term facts relevant to ``message``: gated keyword/semantic hits + graph-linked facts.

    Returns ``(facts, layer)``. ``layer`` names the retrieval layer(s) that actually contributed —
    e.g. ``"semantic"``, ``"fts"``, ``"keyword"``, ``"keyword+graph"`` — or None when nothing was
    recalled. It reflects real hits (never guessed): a layer that returns nothing is not listed.

    ``project`` narrows what may be recalled to that folder's facts plus the ones that belong
    everywhere. It defaults to :data:`EVERY_PROJECT` — no narrowing — because a conversation with no
    folder open is not a project; a surface that has one passes it.

    ``gate`` defaults to a real :class:`MemoryGate`. Pass ``None`` to opt out explicitly.
    """
    if gate is _GATE_UNSET:
        gate = MemoryGate()
    facts: list[str] = []
    layers: list[str] = []
    if memory is not None:
        captured: dict[str, str] = {}
        def on_layer(name: str) -> None:
            captured["layer"] = name

        if search is not None:
            items = search(message, on_layer)
        else:
            # Graded, one keyword at a time. A single try/except around both was measured to
            # drop `on_layer` whenever `project` was unsupported: the facts still arrived and the
            # layer came back None, so the UI reported "nothing contributed" about a recall that
            # had. A tolerance that swallows the wrong argument reports a lie instead of failing.
            try:
                items = memory.search(message, k=k, on_layer=on_layer, project=project)
            except TypeError:
                try:
                    items = memory.search(message, k=k, on_layer=on_layer)
                except TypeError:  # a minimal SupportsRecall fake that accepts neither
                    items = memory.search(message, k=k)
        if gate is not None:
            items = gate.filter(items, message)  # admission gate (trust boundary)
        if items:
            # Surface trust provenance on recall, exactly as the autonomous readback and the persona
            # preamble do: a fact learned from untrusted content must not read to the model as
            # verified. Dropping the label here (taking .content raw) was a taint leak — a poisoned
            # memory could re-enter the next turn's prompt looking clean.
            facts = [
                item.content
                + (
                    " [unverified: learned from untrusted content]"
                    if getattr(item, "provenance", "clean") == "tainted"
                    else ""
                )
                for item in items
            ]
            if "layer" in captured:
                layers.append(captured["layer"])
    if graph is not None:
        # Entity-aware recall: facts linked (via the graph) to entities named in the message, even
        # when they share no keyword with it. Deduped against keyword hits.
        graph_added = 0
        for related in graph.related_facts(message, k=k):
            # Entity-linked facts skip the keyword-similarity gate (they intentionally may not
            # overlap the query), but they must STILL pass the injection check — a graph-reachable
            # tainted memory could otherwise inject override text the gate exists to block.
            if related not in facts and (gate is None or gate.is_clean(related)):
                facts.append(related)
                graph_added += 1
        if graph_added:
            layers.append("graph")
    return facts, ("+".join(layers) if layers else None)
