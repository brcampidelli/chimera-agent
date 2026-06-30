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
    "result, decide whether it correctly and completely accomplishes the task.\n"
    "Reply with ONE line and nothing else — no preamble, reasoning, or markdown:\n"
    "- If it is correct and complete, reply exactly: APPROVED\n"
    "- Otherwise reply: REVISE: <specific, actionable feedback on what to fix>"
)


@dataclass
class Review:
    """The Manager's verdict on a Worker result."""

    approved: bool
    feedback: str = ""


class Manager:
    """Reviews a Worker's result and approves or requests a revision.

    With ``use_rubric`` the verdict is the **cascade rubric** (DailyReport): the result
    is scored on instruction-following → factuality → rationality and approved on the
    importance-weighted overall, with feedback naming the weakest dimension. Otherwise a
    single APPROVED/REVISE verdict is used.
    """

    def __init__(
        self,
        backend: SupportsComplete,
        model: str | None = None,
        *,
        use_rubric: bool = False,
        rubric_threshold: float = 0.6,
    ) -> None:
        self.backend = backend
        self.model = model
        self.use_rubric = use_rubric
        self.rubric_threshold = rubric_threshold

    def review(self, task: str, proposed: str, *, context: str = "") -> Review:
        if self.use_rubric:
            return self._review_rubric(task, proposed)
        user = f"Task:\n{task}\n\nWorker's result:\n{proposed}"
        if context:
            user = f"{context}\n\n{user}"
        verdict = self.backend.complete(
            [Message(role="system", content=_MANAGER_SYSTEM), Message(role="user", content=user)],
            model=self.model,
            temperature=0.0,
        ).content.strip()
        return self._parse_verdict(verdict)

    def _review_rubric(self, task: str, proposed: str) -> Review:
        from chimera.eval.rubric import cascade_dimensions, evaluate_cascade, model_judge

        dims = cascade_dimensions(model_judge(self.backend, self.model))
        result = evaluate_cascade(proposed, task, dims)
        if result.overall >= self.rubric_threshold:
            return Review(approved=True)
        worst = min(result.scores, key=lambda name: result.scores[name]) if result.scores else "?"
        score = result.scores.get(worst, 0.0)
        feedback = (
            f"Weakest dimension: {worst} ({score:.2f}); overall {result.overall:.2f} "
            f"< {self.rubric_threshold:.2f}. Strengthen that aspect."
        )
        return Review(approved=False, feedback=feedback)

    @staticmethod
    def _parse_verdict(verdict: str) -> Review:
        """Robustly read APPROVED / REVISE even with markdown or a short preamble.

        Models routinely wrap the verdict (``**APPROVED**``, ``Looks good. APPROVED``),
        and a strict prefix match misreads those as rejections — which, under
        verify-or-revert, would revert correct work. So we strip leading markup and,
        failing a clean prefix, scan the whole reply (REVISE wins over APPROVED).
        """
        cleaned = verdict.lstrip("*_#>-` \t\r\n")
        upper = cleaned.upper()
        if upper.startswith("APPROVED"):
            return Review(approved=True)
        if upper.startswith("REVISE"):
            feedback = cleaned.split(":", 1)[1].strip() if ":" in cleaned else cleaned
            return Review(approved=False, feedback=feedback)

        full = verdict.upper()
        if "REVISE" in full:
            tail = verdict[full.find("REVISE") + len("REVISE") :].lstrip(": \t")
            return Review(approved=False, feedback=tail.strip() or verdict)
        if "APPROVED" in full:
            return Review(approved=True)
        return Review(approved=False, feedback=verdict)
