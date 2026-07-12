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
from typing import Protocol

from chimera.core.agent import AgentResult, ToolActivity
from chimera.memory.gate import MemoryGate
from chimera.memory.models import MemoryItem


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


class SupportsRecall(Protocol):
    """Long-term memory: keyword recall over stored facts."""

    def search(self, query: str, *, k: int = 5) -> list[MemoryItem]: ...


class SupportsRelated(Protocol):
    """Graph memory: recall facts linked to entities mentioned in the query."""

    def related_facts(self, query: str, k: int = 5) -> list[str]: ...


@dataclass
class ChatTurn:
    """One exchange in the conversation."""

    user: str
    assistant: str


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
    steps: int = 0
    stopped_reason: str = ""


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
    turns: list[ChatTurn] = field(default_factory=list)

    def send(self, message: str) -> str:
        """Run one user message through the agent and record the exchange."""
        answer = self.agent.run(self._compose(message)).answer
        self._record(message, answer)
        return answer

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
        facts, layer = self._recall(message)
        result = self.agent.run(self._assemble(message, facts), on_token=on_token, on_tool=on_tool)
        self._record(message, result.answer)
        return TurnReport(
            answer=result.answer,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
            usd=result.usd,
            tool_names=list(result.tool_names),
            memory_facts_used=len(facts),
            memory_layer=layer,
            steps=result.steps,
            stopped_reason=result.stopped_reason,
        )

    def _record(self, message: str, answer: str) -> None:
        self.turns.append(ChatTurn(user=message, assistant=answer))
        # Bound the transcript: only the last ``max_history`` turns ever reach the prompt, so a
        # long-lived session (TUI / reused gateway) must not grow this list without limit.
        cap = max(50, self.max_history * 4)
        if len(self.turns) > cap:
            del self.turns[:-cap]

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
        """Recall long-term facts for this message: gated keyword/semantic hits + graph-linked facts.

        Returns ``(facts, layer)``. ``layer`` names the retrieval layer(s) that actually contributed —
        e.g. ``"semantic"``, ``"fts"``, ``"keyword"``, ``"keyword+graph"`` — or None when nothing was
        recalled. It reflects real hits (never guessed): a layer that returns nothing is not listed.
        """
        facts: list[str] = []
        layers: list[str] = []
        if self.memory is not None:
            captured: dict[str, str] = {}
            items = self._memory_search(message, lambda name: captured.__setitem__("layer", name))
            if self.gate is not None:
                items = self.gate.filter(items, message)  # admission gate (trust boundary)
            if items:
                facts = [item.content for item in items]
                if "layer" in captured:
                    layers.append(captured["layer"])
        if self.graph is not None:
            # Entity-aware recall: facts linked (via the graph) to entities named in the
            # message, even when they share no keyword with it. Deduped against keyword hits.
            graph_added = 0
            for related in self.graph.related_facts(message, k=self.memory_k):
                # Entity-linked facts skip the keyword-similarity gate (they intentionally may not
                # overlap the query), but they must STILL pass the injection check — a graph-reachable
                # tainted memory could otherwise inject override text the gate exists to block.
                if related not in facts and (self.gate is None or self.gate.is_clean(related)):
                    facts.append(related)
                    graph_added += 1
            if graph_added:
                layers.append("graph")
        return facts, ("+".join(layers) if layers else None)

    def _memory_search(
        self, message: str, on_layer: Callable[[str], None]
    ) -> list[MemoryItem]:
        """Call ``memory.search`` capturing the winning layer, tolerating a fake without ``on_layer``."""
        assert self.memory is not None
        try:
            return self.memory.search(message, k=self.memory_k, on_layer=on_layer)  # type: ignore[call-arg]
        except TypeError:  # a minimal SupportsRecall fake that doesn't accept on_layer
            return self.memory.search(message, k=self.memory_k)

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
        if self.turns:
            recent = self.turns[-self.max_history :]
            convo = "\n".join(f"User: {t.user}\nAssistant: {t.assistant}" for t in recent)
            parts.append("Conversation so far:\n" + convo)
        parts.append(f"User: {message}")
        return "\n\n".join(parts)
