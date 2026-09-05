"""Context as a budget, and compaction that does not lose the thread.

Before this, the message list only ever grew. Overflow was terminal: the provider raised, the
failover table mapped CONTEXT_OVERFLOW to ABORT, and the caller got the raw provider error. That
made the context window — not the difficulty of the task — the real ceiling on what the agent could
do, and it made ``--max-steps`` a dangerous knob that looks harmless: raising it is the obvious move
when a task doesn't finish, and it is exactly what kills the run.

Two ideas do the work here, and the second is the one people skip.

**A budget below the real window.** The model advertises 200K; we spend against 120K and keep the
rest for the answer and for the margin between an estimate and the truth. Compaction triggers on the
budget, not on the window, so the trigger fires while there is still room to compact *into*.

**Active restoration.** Compressing is the easy half. An agent that compacts and then no longer
knows which file it was editing, what the plan was, or what it had already finished is worse than
one that never compacted — it keeps working, confidently, on the wrong thing. So after compaction we
re-inject what the run needs to still be itself: the working file, the plan, the task list, and a
short statement of where it had got to.

The calibration rule is directional and worth stating because it is easy to get backwards:
**maximise recall first, then tighten precision.** Removing too much early produces an agent that
loses context it needed later, and that loss is unrecoverable — the messages are gone. Keeping too
much merely costs tokens, which is a bill, not a failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.providers.gateway import MessageLike
from chimera.telemetry import get_logger

_log = get_logger("core.context_budget")

#: Fraction of the model's advertised window we are willing to spend on the prompt. The remainder
#: covers the completion plus the gap between a token estimate and the provider's real count.
DEFAULT_BUDGET_FRACTION = 0.6

#: Compact once the prompt passes this share of the BUDGET (not of the window). Firing at the window
#: is firing too late: compaction itself needs room to work.
DEFAULT_TRIGGER = 0.8

#: Turns kept verbatim at the tail. Recent exchanges are where the current sub-task lives, and
#: summarising them is what makes an agent forget what it is doing right now.
DEFAULT_KEEP_RECENT = 6

#: Fallback window when the model is not in the catalog. Deliberately modest: under-estimating costs
#: an unnecessary compaction, over-estimating costs a dead run.
FALLBACK_CONTEXT_TOKENS = 128_000

#: Rough chars-per-token. Only used before the provider has reported a real count.
_CHARS_PER_TOKEN = 4


def window_tokens(model: str) -> int:
    """The model's context window: the catalogue first, then the live index, then the fallback.

    The catalogue goes first because its nineteen entries are checked by hand against what the
    provider actually SERVES, which is not always what it advertises — `top_provider.context_length`
    and `context_length` disagree for 39 of the 431 models the index publishes, by up to 20%.

    The live index goes second, and adding it is the point of this function's second life. Before,
    anything outside those nineteen got a flat 128,000, and the constant's own comment names what
    that costs: *"over-estimating costs a dead run"*, because a context overflow maps to ABORT with
    no recovery. Measured against the index on 2026-09-05: **31 of 431 models have a window at or
    below 64,000**, which is under the trigger that 128,000 produces — so for those the budget would
    never fire before the wall it exists to avoid. The number was never unknown; nothing had written
    it down.

    The constant remains for the case where neither knows: a slug never fetched, on an install whose
    cache has never been warmed.
    """
    from chimera.providers.catalog import CATALOG

    slug = (model or "").strip()
    if not slug:
        return FALLBACK_CONTEXT_TOKENS
    for entry in CATALOG:
        # Match on the tail so "openrouter/anthropic/claude-x" finds "claude-x".
        if entry.slug == slug or slug.endswith(entry.slug.split("/")[-1]):
            return entry.context_k * 1000
    try:
        from chimera.providers.listing import known_window

        remembered = known_window(slug)
    except Exception:  # noqa: BLE001 — see below; a look-up that cannot run is a missing answer
        # NOT for a corrupt cache file: `listing._remembered` already turns that into None, and a
        # sabotage narrowing this catch failed to redden a single test, which is how a guard
        # announces it is guarding nothing. What it does catch is the look-up being unavailable
        # at all — an import that fails on a partial install, or `get_settings()` raising on a
        # malformed config. Sizing a context must not be the thing that takes a run down.
        remembered = None
    return remembered or FALLBACK_CONTEXT_TOKENS


def estimate_tokens(messages: list[MessageLike]) -> int:
    """Cheap size estimate for a message list, used only before a real count exists.

    Deliberately not a tokenizer: adding one buys accuracy we do not need for a threshold decision,
    and costs a dependency plus per-step CPU. Once the provider answers, its own count replaces this.
    """
    total = 0
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += len(str(content))
        # Tool-call payloads ride on the assistant message and are not in `content`.
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if calls:
            total += len(str(calls))
    return total // _CHARS_PER_TOKEN


@dataclass
class RunState:
    """What the run must still know about itself after its history has been compressed.

    This is the "active restoration" half. Every field here answers a question an agent asks
    implicitly on every step, and that a naive summary silently drops.
    """

    #: What the run was ASKED to do, verbatim.
    #:
    #: The first thing a naive compactor drops and the last thing an agent can do without. The task
    #: arrives as a user message at the end of the initial prompt; after enough turns it falls out
    #: of the kept tail, and `_structural_note` deliberately does not summarise content — so the
    #: agent is left executing a plan whose purpose was deleted, with a note saying N messages were
    #: removed. A file it can re-read; an instruction it cannot.
    #:
    #: Set by :meth:`Agent.run` itself rather than by each caller, because the callers that most
    #: need it are the ones nobody remembered to update: `/api/runs` populated plan and tasks, and
    #: the conversational coding turn — the screen that compacts most — set only ``open_file``.
    #:
    #: That default is a copy of whatever string ``run`` was called with, which is right for a chat
    #: turn and wrong for the autonomous loop, where the argument is a composed prompt carrying the
    #: plan and the whole retrieved-context block. So ``AutonomousAgent`` writes the raw request
    #: here first and ``run`` leaves it alone. Anything else that prompts an agent with an assembled
    #: string owes this field the request that string was assembled FROM.
    #:
    #: Two independent papers measure this failure class (arXiv 2608.11242: compactors retain 17%
    #: of injected session constraints; 2608.11392: rule-form items survive a compaction far better
    #: than facts). Our system message already survives verbatim, which is the half those papers
    #: find missing; this is the other half.
    task: str = ""
    #: Path of the file currently being worked on, and its content — re-read, not remembered.
    open_file: tuple[str, str] | None = None
    #: The plan the run is executing — the CURRENT one. Refreshed per attempt by the loop that owns
    #: the plan, because a run that re-plans on a stall would otherwise restore the steps it just
    #: abandoned: worse than restoring nothing, since the agent then works confidently from them.
    plan: str = ""
    #: Task list with status, so finished work is not redone. Status is the point: a bare copy of
    #: the plan's steps asserts that none are done, which is a claim, not a blank.
    tasks: list[str] = field(default_factory=list)
    #: One paragraph: what the agent was doing and how far it had got.
    current_state: str = ""

    def as_message(self) -> MessageLike | None:
        """Render as a single user message, or None when there is nothing to restore."""
        parts: list[str] = []
        # First, because it is what everything else is in service of. A restored plan under a
        # forgotten task is an agent carrying out steps it can no longer justify.
        if self.task:
            parts.append(f"The task you were given:\n{self.task}")
        if self.current_state:
            parts.append(f"Current state:\n{self.current_state}")
        if self.plan:
            parts.append(f"Plan:\n{self.plan}")
        if self.tasks:
            parts.append("Tasks:\n" + "\n".join(f"- {t}" for t in self.tasks))
        if self.open_file:
            path, content = self.open_file
            parts.append(f"File currently being edited — {path}:\n{content}")
        if not parts:
            return None
        return {
            "role": "user",
            "content": (
                "[context restored after compaction — the conversation above was summarised]\n\n"
                + "\n\n".join(parts)
            ),
        }


@dataclass
class ContextBudget:
    """Decides when the prompt is too big, and by how much it must shrink."""

    window: int
    fraction: float = DEFAULT_BUDGET_FRACTION
    trigger: float = DEFAULT_TRIGGER

    @classmethod
    def for_model(cls, model: str, **kwargs: Any) -> ContextBudget:
        return cls(window=window_tokens(model), **kwargs)

    @property
    def budget(self) -> int:
        """Tokens we are willing to spend on the prompt."""
        return int(self.window * self.fraction)

    @property
    def threshold(self) -> int:
        """Prompt size at which compaction fires."""
        return int(self.budget * self.trigger)

    def should_compact(self, prompt_tokens: int) -> bool:
        return prompt_tokens >= self.threshold

    def headroom(self, prompt_tokens: int) -> int:
        """Tokens left before the trigger. Negative once it has been crossed."""
        return self.threshold - prompt_tokens


def compact(
    messages: list[MessageLike],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
    state: RunState | None = None,
    summarise: Any = None,
) -> tuple[list[MessageLike], bool]:
    """Shrink a message list, preserving what the run needs to continue.

    Returns ``(messages, changed)``. ``changed`` is False when there was nothing worth compacting —
    callers should treat a no-op as "this did not help" rather than retrying into the same wall.

    The system message is never touched: it is the stable prefix the whole prompt cache is keyed on,
    and rewriting it invalidates every cached turn behind it.

    ``summarise`` is an optional ``(list[MessageLike]) -> str``. Without it the compressed span is
    replaced by a structural note rather than a model-written summary — cheaper, and honest about
    what it is. With it, the caller supplies the model call, so this module stays free of a backend.
    """
    if len(messages) <= keep_recent + 1:  # +1 for the system message
        return messages, False

    head: list[MessageLike] = []
    body_start = 0
    if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
        head = [messages[0]]
        body_start = 1

    body = messages[body_start:]
    if len(body) <= keep_recent:
        return messages, False

    older, recent = body[:-keep_recent], body[-keep_recent:]
    # A tail that begins with an orphaned tool result is a malformed prompt: most providers reject a
    # `tool` message whose matching assistant tool_call is no longer present. Walk forward until the
    # tail starts on a message that can legally open a conversation turn.
    while recent and isinstance(recent[0], dict) and recent[0].get("role") == "tool":
        older.append(recent.pop(0))
    if not older or not recent:
        return messages, False

    summary = summarise(older) if summarise is not None else _structural_note(older)
    compacted: list[MessageLike] = [
        *head,
        {"role": "user", "content": f"[earlier conversation, compacted]\n{summary}"},
    ]
    if state is not None:
        restored = state.as_message()
        if restored is not None:
            compacted.append(restored)
    compacted.extend(recent)

    _log.debug("compacted %d messages into 1 summary (+%d recent)", len(older), len(recent))
    return compacted, True


def _structural_note(older: list[MessageLike]) -> str:
    """A factual description of what was dropped, used when no summariser is supplied.

    Deliberately not an invented summary: claiming to preserve content we never read would be worse
    than saying plainly that N exchanges and these tools happened. The agent can re-read a file; it
    cannot un-believe a fabricated summary.
    """
    tools: list[str] = []
    for message in older:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            name = call.get("function", {}).get("name") if isinstance(call, dict) else None
            if name:
                tools.append(str(name))
    unique = sorted(set(tools))
    note = f"{len(older)} earlier messages were removed to free context."
    if unique:
        note += f" Tools used in that span: {', '.join(unique)}."
    note += " Re-read any file you need rather than relying on memory of it."
    return note
