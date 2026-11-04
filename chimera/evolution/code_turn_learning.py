"""What a Code-screen turn learns, and whether the next turn is told.

The Code screen runs a plain ``Agent``, which never wrote to the experience buffer. The lessons were
recorded by the autonomous paths only, so the most common thing the app does — a coding turn — left
nothing behind. This module is the one place that decides what a turn writes and what it reads back,
so the endpoint carries two calls and no policy of its own.

Lessons go to their own store, ``code_lessons.json``. ``AutonomousAgent`` reads ``experience.json``
unconditionally — lanes, workflows, the lifecycle crew and ``chimera solve`` all do — so a lesson
written there would reach those prompts with the flag off. Only the Code-turn recall reads this file.

Recording is always on: every turn that ran a loop leaves one lesson. Reading is the flag
``CHIMERA_SKILL_CARDS``, the switch that already decides whether learned cards reach a prompt — one
question for the owner ("does what the agent learned go into my prompt?"), not two. It stays off by
default, because the learn→use measurement in ``bench/learning_lift`` (seven pre-registered runs)
has not shown that reading lessons helps, so a default that read them would claim what the bench
does not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.evolution.experience import Experience, ExperienceBuffer, Outcome, format_lessons
from chimera.governance.sanitize import sanitize_untrusted
from chimera.telemetry import get_logger

_log = get_logger("evolution.code_turn")

#: The store only the Code screen reads. Named apart from ``experience.json`` on purpose.
CODE_LESSONS_FILE = "code_lessons.json"
#: Caps applied at record time. A task is one line of a person's request; an answer is a summary.
MAX_TASK_CHARS = 300
MAX_DETAIL_CHARS = 500

#: Verifier states that mean the check ran and the work failed it.
_FAILED_STATES = frozenset({"failed", "conflict"})


def _outcome(verdict: str) -> Outcome:
    """What a turn's verdict says it was.

    ``passed`` is a success and ``failed``/``conflict`` a failure — both are the check speaking. Any
    other state (no check configured, unverified, abstained) is recorded as ``unverified``: kept and
    labelled, but not evidence, so it never counts toward recurrence and can never mint a card.
    """
    if verdict == "passed":
        return "success"
    if verdict in _FAILED_STATES:
        return "failure"
    return "unverified"


def record_turn_lesson(
    home: Path, *, task: str, answer: str, verdict: str, tainted: bool, steps: int
) -> Experience | None:
    """Append one lesson for this turn to the Code store, or None when there is nothing to keep.

    ``steps == 0`` means a gate stopped the turn before the model ran (plan gate, commands refused):
    nothing was tried, so there is nothing to learn from. An empty answer with no check is nothing
    either. ``tainted`` is the run's own ledger state; the label is kept on the record, as every
    other durable write in the codebase keeps it, and the Code recall leaves tainted lessons out.
    """
    if steps <= 0 or not task.strip() or (not answer.strip() and _outcome(verdict) == "unverified"):
        return None
    detail = f"[check: {verdict or 'none'}] {answer.strip()}"[:MAX_DETAIL_CHARS]
    return ExperienceBuffer(home / CODE_LESSONS_FILE).record(
        task.strip()[:MAX_TASK_CHARS], _outcome(verdict), detail=detail, tainted=tainted
    )


def lesson_block(home: Path, task: str) -> str:
    """The clean lessons from the same work as ``task``, as a prompt block — "" when there are none.

    Only the same work (``recurring``), not any related one. Tainted lessons are left out: a tainted
    fact or lesson in the autonomous path arms the taint ledger when it is read, and this recall has
    no ledger to arm, so it must not carry untrusted-derived text into a prompt unlabelled-and-unarmed.
    """
    items = [
        exp for exp in ExperienceBuffer(home / CODE_LESSONS_FILE).recurring(task)
        if exp.provenance != "tainted"
    ]
    return sanitize_untrusted(format_lessons(items))


class CodeTurnRecall:
    """What a Code turn's prompt reads back: the learned cards, then the lessons.

    Duck-typed like ``CardRetriever`` for the one method the agent calls (``card_context``). Each part
    is best-effort on its own: a card store that cannot be read must not cost the lessons, and the
    reverse. The whole object exists only when the flag is on — with it off, the agent gets no
    retriever at all and nothing is read.
    """

    def __init__(self, cards: Any, home: Path) -> None:
        self.cards = cards
        self.home = home

    def card_context(self, task: str) -> str:
        parts: list[str] = []
        if self.cards is not None:
            try:
                parts.append(str(self.cards.card_context(task) or ""))
            except Exception as exc:  # noqa: BLE001 -- advice, never a turn failure
                _log.debug("skill-card retrieval skipped: %s", type(exc).__name__)
        try:
            parts.append(lesson_block(self.home, task))
        except Exception as exc:  # noqa: BLE001 -- recall is advisory; the turn goes on without it
            _log.debug("lessons unavailable for this turn: %s", type(exc).__name__)
        return "\n\n".join(part for part in parts if part)
