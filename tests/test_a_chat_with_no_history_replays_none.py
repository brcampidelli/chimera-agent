"""`max_history=0` means "replay no earlier turns". It replayed all of them: the window was spelled
`turns[-max_history:]`, and `turns[-0:]` is the whole list. Found by the study-25 chat-history bench;
no caller sets 0 today, which is why nothing noticed."""

from __future__ import annotations

from typing import Any

from chimera.interface.session import ChatSession, ChatTurn, recent_turns


def _turns(n: int) -> list[ChatTurn]:
    return [ChatTurn(user=f"question {i}", assistant=f"answer {i}") for i in range(n)]


def test_the_window_is_empty_at_zero_and_the_tail_otherwise() -> None:
    turns = _turns(8)
    assert recent_turns(turns, 0) == []
    assert recent_turns(turns, 3) == turns[-3:]
    assert recent_turns(turns, 20) == turns
    assert recent_turns([], 6) == []


class _Echo:
    def __init__(self) -> None:
        self.tasks: list[str] = []

    def run(self, task: str, **_: Any) -> Any:
        from chimera.core.agent import AgentResult

        self.tasks.append(task)
        return AgentResult(answer="ok", steps=1, stopped_reason="final", transcript=[])


def test_a_session_with_no_history_sends_no_earlier_turn() -> None:
    agent = _Echo()
    session = ChatSession(agent=agent, max_history=0)  # type: ignore[arg-type]
    session.turns.extend(_turns(4))
    session.send("the new question")
    sent = agent.tasks[-1]
    assert "question 0" not in sent and "answer 3" not in sent
    assert "the new question" in sent
