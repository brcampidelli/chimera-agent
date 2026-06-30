"""The conversational session core.

``ChatSession`` turns the single-shot agent into a multi-turn assistant: it keeps
a rolling transcript, optionally recalls relevant long-term memory, and composes
both into each turn's prompt. It depends only on small protocols, so a fake agent
and memory make it fully testable without a network — and the real CLI ``chat``
command, the TUI, and the messaging gateway all reuse it unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from chimera.core.agent import AgentResult
from chimera.memory.gate import MemoryGate
from chimera.memory.models import MemoryItem


class SupportsRun(Protocol):
    """The agent loop: turn a task into a result with a final answer."""

    def run(self, task: str) -> AgentResult: ...


class SupportsRecall(Protocol):
    """Long-term memory: keyword recall over stored facts."""

    def search(self, query: str, *, k: int = 5) -> list[MemoryItem]: ...


@dataclass
class ChatTurn:
    """One exchange in the conversation."""

    user: str
    assistant: str


@dataclass
class ChatSession:
    """Multi-turn, memory-aware conversation over a tool-using agent."""

    agent: SupportsRun
    memory: SupportsRecall | None = None
    gate: MemoryGate | None = field(default_factory=MemoryGate)
    max_history: int = 6
    memory_k: int = 3
    turns: list[ChatTurn] = field(default_factory=list)

    def send(self, message: str) -> str:
        """Run one user message through the agent and record the exchange."""
        answer = self.agent.run(self._compose(message)).answer
        self.turns.append(ChatTurn(user=message, assistant=answer))
        return answer

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

    def _compose(self, message: str) -> str:
        parts: list[str] = []
        if self.memory is not None:
            items = self.memory.search(message, k=self.memory_k)
            if self.gate is not None:
                items = self.gate.filter(items, message)  # admission gate (trust boundary)
            facts = [item.content for item in items]
            if facts:
                parts.append("Relevant facts from memory:\n" + "\n".join(f"- {f}" for f in facts))
        if self.turns:
            recent = self.turns[-self.max_history :]
            convo = "\n".join(f"User: {t.user}\nAssistant: {t.assistant}" for t in recent)
            parts.append("Conversation so far:\n" + convo)
        parts.append(f"User: {message}")
        return "\n\n".join(parts)
