"""Worker-Manager supervision (generate-vs-verify).

The Worker (the agent loop) produces a result; the Manager — a separate model role —
reviews it against the task and either approves or asks for a revision with specific
feedback. Separating generation from review cuts error propagation (per AdvancedShelLM).
"""

from __future__ import annotations

from dataclasses import dataclass

from chimera.providers.gateway import Message, SupportsComplete

_MANAGER_SYSTEM = (
    "You are a strict reviewer (the Manager). Given a task and the Worker's proposed "
    "result, decide whether it correctly and completely accomplishes the task. "
    "If it does, reply with exactly 'APPROVED'. Otherwise reply 'REVISE: ' followed by "
    "specific, actionable feedback on what to fix."
)


@dataclass
class Review:
    """The Manager's verdict on a Worker result."""

    approved: bool
    feedback: str = ""


class Manager:
    """Reviews a Worker's result and approves or requests a revision."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def review(self, task: str, proposed: str, *, context: str = "") -> Review:
        user = f"Task:\n{task}\n\nWorker's result:\n{proposed}"
        if context:
            user = f"{context}\n\n{user}"
        verdict = self.backend.complete(
            [Message(role="system", content=_MANAGER_SYSTEM), Message(role="user", content=user)],
            model=self.model,
            temperature=0.0,
        ).content.strip()

        if verdict.upper().startswith("APPROVED"):
            return Review(approved=True)
        feedback = verdict
        if verdict.upper().startswith("REVISE") and ":" in verdict:
            feedback = verdict.split(":", 1)[1].strip()
        return Review(approved=False, feedback=feedback)
