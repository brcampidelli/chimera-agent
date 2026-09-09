"""A store whose command says "saved as you go" was throwing away the beginning.

``ChatSession._record`` capped the in-memory transcript at ``max(50, max_history * 4)`` and
``SessionManager.persist`` wrote ``session.turns`` — the capped list — so from turn 51 every save
rewrote the file **without turn 1**. Nothing failed and nothing said so; the file simply became a
sliding window over the last fifty turns of a conversation the command promised to keep.

The cap was not wrong, it was in the wrong place. Only ``max_history`` turns ever reach a prompt
(``_assemble`` slices), so bounding the PROMPT costs nothing and bounding the RECORD costs the
record. The one surface that needs a bound on the record is the one with no store and no end: the
messaging gateway keeps a live session per chat for as long as the process runs, and that is where
the bound now lives — stated, rather than applied to everyone and noticed by nobody.
"""

from __future__ import annotations

from pathlib import Path

from chimera.api.sessions import SessionManager, SessionStore
from chimera.core.agent import AgentResult
from chimera.interface.session import ChatSession
from chimera.server.gateway import InboundMessage, MessageGateway


class _Agent:
    def __init__(self) -> None:
        self.n = 0

    def run(self, task: str, *, on_token=None, on_tool=None) -> AgentResult:  # type: ignore[no-untyped-def]
        self.n += 1
        return AgentResult(answer=f"reply#{self.n}", steps=1, stopped_reason="final")


def test_the_first_turn_is_still_on_disk_after_the_fifty_first(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    manager = SessionManager(lambda: ChatSession(_Agent()), store)
    active = manager.new()
    session = manager.get(active)

    for i in range(51):
        session.send(f"msg{i}")
        manager.persist(active)

    saved = store.load(active)
    assert len(saved) == 51
    assert saved[0].user == "msg0"


def test_the_prompt_is_still_bounded_by_max_history() -> None:
    """Keeping the whole record must not put the whole record in the prompt — that is the cost
    the cap was there to avoid, and it is paid by the slice in `_assemble`, not by the file."""

    class _Recording(_Agent):
        def __init__(self) -> None:
            super().__init__()
            self.prompts: list[str] = []

        def run(self, task: str, *, on_token=None, on_tool=None):  # type: ignore[no-untyped-def]
            self.prompts.append(task)
            return super().run(task)

    agent = _Recording()
    session = ChatSession(agent, max_history=2)
    for i in range(60):
        session.send(f"msg{i}")

    assert len(session.turns) == 60  # the record is whole
    assert "msg0" not in agent.prompts[-1] and "msg58" in agent.prompts[-1]  # the prompt is not


def test_the_gateway_still_bounds_a_session_nothing_ever_saves() -> None:
    """A chat session per Discord/WhatsApp chat, alive for the life of the process, persisted
    nowhere. Unbounded growth there is a leak with no upside — there is no file to be the record.
    """
    gateway = MessageGateway(lambda: ChatSession(_Agent()), max_turns=3)
    for i in range(5):
        gateway.on_message(InboundMessage(text=f"msg{i}", chat_id="alice"))

    turns = gateway.session_for("local:alice").turns
    assert [t.user for t in turns] == ["msg2", "msg3", "msg4"]


def test_a_session_that_asks_for_a_bound_gets_the_one_it_asked_for() -> None:
    session = ChatSession(_Agent(), max_turns=2)
    for i in range(4):
        session.send(f"msg{i}")

    assert [t.user for t in session.turns] == ["msg2", "msg3"]
