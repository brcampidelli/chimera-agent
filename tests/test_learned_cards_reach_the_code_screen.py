"""What the agent learned has to come back on the screen people actually use.

Settings mints skill cards and says of them: "a learned skill is read back when it matches the
task." That was true on `/api/runs`, which builds an `AutonomousAgent` — the one class with a
`cards` seam. The Code screen builds a plain `Agent`, and the Code screen is FIRST in the
navigation rail. So on the surface most people use, nothing the agent had ever learned came back,
while the toggle that mints them said otherwise.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.providers.gateway import CompletionResult
from chimera.tools.registry import ToolRegistry


class _Backend:
    def __init__(self) -> None:
        self.systems: list[str] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        first = messages[0]
        data = first.as_dict() if hasattr(first, "as_dict") else first
        self.systems.append(str(data.get("content", "")))
        return CompletionResult(content="done", model="m", prompt_tokens=1, completion_tokens=1)


class _Cards:
    """Duck-typed like `CardRetriever` for the one method this seam touches."""

    def __init__(self, block: str = "") -> None:
        self.block = block
        self.asked: list[str] = []
        self.last_retrieved: list[str] = []

    def card_context(self, task: str) -> str:
        self.asked.append(task)
        return self.block


def _run(cards: Any, task: str = "rename the loader") -> _Backend:
    backend = _Backend()
    Agent(backend, ToolRegistry(), AgentConfig(inject_skill_context=False), cards=cards).run(task)
    return backend


def test_a_matching_card_reaches_the_prompt() -> None:
    cards = _Cards("Learned: the loader is generated; edit the template instead.")

    backend = _run(cards)

    assert cards.asked == ["rename the loader"], "the retriever is asked about THIS task"
    assert "edit the template instead" in backend.systems[0]


def test_the_card_comes_after_the_built_in_skills_and_before_the_project() -> None:
    """Order is the argument.

    A learned card came from a run that actually worked HERE, so it is more specific than a built-in
    skill. The project's own conventions are more specific still — `agents_md` already makes that
    case for itself — and the owner's instructions are last because a repository is a convention and
    this is the owner.
    """
    cards = _Cards("CARD-MARKER")
    backend = _Backend()

    Agent(
        backend,
        ToolRegistry(),
        AgentConfig(system_prompt="BASE", inject_skill_context=False, instructions="OWNER-MARKER"),
        cards=cards,
    ).run("go")

    system = backend.systems[0]
    assert system.index("BASE") < system.index("CARD-MARKER") < system.index("OWNER-MARKER")


def test_no_retriever_changes_nothing() -> None:
    """The default, and every caller that predates this seam."""
    backend = _Backend()

    Agent(backend, ToolRegistry(), AgentConfig(system_prompt="BASE", inject_skill_context=False)).run("go")

    assert backend.systems[0] == "BASE"


def test_an_empty_result_adds_no_empty_section() -> None:
    """A heading with nothing under it costs tokens on every turn to convey nothing."""
    with_cards = _run(_Cards(""))
    without = _run(None)

    assert with_cards.systems[0] == without.systems[0]


def test_a_retriever_that_raises_does_not_take_the_turn_down() -> None:
    """The answer is the product; the cards are advice about it."""

    class _Broken:
        last_retrieved: list[str] = []

        def card_context(self, task: str) -> str:
            raise RuntimeError("card store unreadable")

    backend = _run(_Broken())

    assert backend.systems, "the turn must still have happened"


def test_the_code_turn_asks_for_a_retriever_only_when_the_owner_wants_one() -> None:
    """Off is off. Building one anyway would read cards the owner switched off minting."""
    from chimera.api.code_api import _card_retriever
    from chimera.config import Settings

    off = Settings(CHIMERA_SKILL_CARDS=False)  # type: ignore[call-arg]
    assert _card_retriever(off, None) is None


def test_the_code_turn_never_mints_from_this_path() -> None:
    """Reading, never writing.

    Minting a card credits a skill for an outcome, and this path has no verify-or-revert signal to
    credit it with. `build_evolution_context` is asked for the retriever with `evolve_skills=False`
    for exactly that reason.
    """
    import inspect

    from chimera.api import code_api

    source = inspect.getsource(code_api._card_retriever)
    assert "evolve_skills=False" in source
