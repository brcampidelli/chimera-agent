"""The plan frame names the approval question that holds the turn on it.

Before this, the desktop paired a plan with its question by matching the text of the question against
the plan's steps. The frame carried no id, so a question whose wording drifted (a "why" line added on
top, a different step list in a re-asked card) silently detached the plan from its buttons. The id is
the pairing the server already has: the question `ask` writes is the one that holds the turn, and the
gate is the only place that knows both the plan and that question. These tests pin that the id travels
on the plan, and that it is added rather than renamed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chimera.api import plan_gate
from chimera.core.planner import Plan


@dataclass
class _Reply:
    content: str


class _Backend:
    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, messages: list[Any], **kwargs: Any) -> _Reply:
        return _Reply(self.text)


@dataclass
class _Question:
    id: str


def _asking(answer: bool, question_id: str) -> Any:
    """An injected `ask` that announces a question with `question_id`, as `ask_durably` does."""

    def ask(home: Any, action: str, reason: str, **kwargs: Any) -> bool:
        if kwargs.get("on_asked") is not None:
            kwargs["on_asked"](_Question(id=question_id))
        return answer

    return ask


PLAN_TEXT = "1. Ler src/a.ts\n2. Editar src/a.ts"


def test_plan_payload_keeps_the_existing_fields_and_adds_the_question_id() -> None:
    plan = Plan(steps=["Ler src/a.ts", "Editar src/a.ts"], raw=PLAN_TEXT)

    payload = plan_gate.plan_payload(plan, "q-42")

    assert payload == {"steps": ["Ler src/a.ts", "Editar src/a.ts"], "raw": PLAN_TEXT, "approval_id": "q-42"}


def test_plan_payload_without_a_question_has_no_approval_id() -> None:
    plan = Plan(steps=["Ler src/a.ts"], raw="1. Ler src/a.ts")

    assert "approval_id" not in plan_gate.plan_payload(plan)


def test_the_gate_hands_the_plan_and_the_question_id_it_was_asked_under() -> None:
    seen: list[tuple[Plan, str]] = []
    verdict = plan_gate.gate(
        "arruma o cabeçalho",
        home=None,  # type: ignore[arg-type]  # the injected ask never reads it
        backend=_Backend(PLAN_TEXT),
        wait_seconds=1.0,
        ask=_asking(True, "q-42"),
        on_asked=lambda q: None,  # as the turn passes it: the question is announced to a screen
        on_plan_asked=lambda p, qid: seen.append((p, qid)),
    )

    assert verdict.approved is True
    assert len(seen) == 1
    plan, question_id = seen[0]
    assert question_id == "q-42"
    assert plan.steps == ["Ler src/a.ts", "Editar src/a.ts"]


def test_the_gate_still_announces_the_question_to_on_asked() -> None:
    announced: list[str] = []
    plan_gate.gate(
        "arruma o cabeçalho",
        home=None,  # type: ignore[arg-type]
        backend=_Backend(PLAN_TEXT),
        wait_seconds=1.0,
        ask=_asking(False, "q-7"),
        on_asked=lambda q: announced.append(q.id),
        on_plan_asked=lambda p, qid: None,
    )

    assert announced == ["q-7"]


def test_a_plan_nobody_was_asked_on_reports_no_question() -> None:
    seen: list[tuple[Plan, str]] = []
    plan_gate.gate(
        "arruma o cabeçalho",
        home=None,  # type: ignore[arg-type]
        backend=_Backend(PLAN_TEXT),
        wait_seconds=1.0,
        ask=lambda home, action, reason, **kwargs: True,  # never calls on_asked
        on_plan_asked=lambda p, qid: seen.append((p, qid)),
    )

    assert seen == []


def test_passing_only_on_plan_asked_leaves_on_asked_as_none() -> None:
    """`ask_durably` reads whether `on_asked` is set to decide where the code is shown
    (`pending._where_the_code_goes`). A callback created here when the caller gave none would turn
    "code nowhere" or "code on the terminal" into "code on the screen only". So the ask must receive
    exactly what it received before this hook existed: `on_asked=None`."""
    received: dict[str, Any] = {}

    def ask(home: Any, action: str, reason: str, **kwargs: Any) -> bool:
        received.update(kwargs)
        return False

    plan_gate.gate(
        "arruma o cabeçalho",
        home=None,  # type: ignore[arg-type]
        backend=_Backend(PLAN_TEXT),
        wait_seconds=1.0,
        ask=ask,
        on_plan_asked=lambda p, qid: None,
    )

    assert received["on_asked"] is None


def test_code_shown_is_unchanged_by_the_hook() -> None:
    """The same question, asked with and without the hook, reports the same `code_shown`."""
    from chimera.governance import pending

    def where(*, with_hook: bool) -> str:
        captured: dict[str, Any] = {}

        def ask(home: Any, action: str, reason: str, **kwargs: Any) -> bool:
            captured["on_asked"] = kwargs.get("on_asked")
            return False

        plan_gate.gate(
            "arruma o cabeçalho",
            home=None,  # type: ignore[arg-type]
            backend=_Backend(PLAN_TEXT),
            wait_seconds=1.0,
            ask=ask,
            on_plan_asked=(lambda p, qid: None) if with_hook else None,
        )
        return pending._where_the_code_goes(None, captured["on_asked"])

    assert where(with_hook=True) == where(with_hook=False)
