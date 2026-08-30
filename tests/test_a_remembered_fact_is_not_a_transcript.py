"""A long-term memory fact must be recallable, not a transcript of the request.

Measured on a real install: four stored facts of 630-950 characters, each one an entire project
brief followed by an entire answer, filed as a single *semantic fact*. They are scoped to their own
folder, so the blast radius is small — but every later run in that folder pays context for them,
and a fact's size is a recurring cost rather than a one-off.

The answer side was already bounded. The task side was not, and the task is the long half.

Free: no model call, no network.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import AgentResult
from chimera.core.autonomous import _FACT_CHARS, AutonomousAgent, AutonomousConfig

BRIEFING = """Leia BRIEF.md e construa a pagina.

Escreva o arquivo index.html na RAIZ desta pasta de trabalho — o mesmo lugar onde estao BRIEF.md
e verificar.py. Nao crie subpastas, nao use caminhos de outros projetos, nao invente uma estrutura
de diretorios. Um unico arquivo, com todo o CSS dentro de uma tag <style>: sem framework, sem CDN,
sem fonte externa, porque a pagina precisa abrir offline. Cumpra o brief item por item."""


class _Worker:
    def run(self, task: str, **kwargs: Any) -> AgentResult:
        return AgentResult(answer="Pronto.", steps=1, stopped_reason="final")


class _Memory:
    """Records what was filed, and under which key."""

    def __init__(self) -> None:
        self.facts: list[tuple[str, str]] = []

    def remember(self, fact: str, *, key: str = "", **kwargs: Any) -> None:
        self.facts.append((fact, key))

    def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def _remember(task: str, answer: str = "Pronto.") -> _Memory:
    memory = _Memory()
    auto = AutonomousAgent(
        _Worker(), memory=memory, config=AutonomousConfig(use_planner=False, use_manager=False)
    )
    auto._remember_success(task, answer)
    return memory


# --- the size ------------------------------------------------------------------------------------


def test_a_brief_is_remembered_by_what_it_asked_for() -> None:
    """The measured case: 769 characters of brief became a 769-character "fact"."""
    fact, _ = _remember(BRIEFING).facts[0]

    assert "Leia BRIEF.md e construa a pagina." in fact
    assert "sem framework, sem CDN" not in fact, "the whole brief is still in the fact"
    assert len(fact) < 250, f"the fact is {len(fact)} characters"


def test_a_long_single_line_task_is_cut_as_well() -> None:
    """First-line-only is not a bound on its own: a task with no line breaks would pass through
    whole, and the longest one measured on the real install was exactly that shape."""
    fact, _ = _remember("x" * 900).facts[0]

    assert len(fact) <= len("Accomplished: ") + _FACT_CHARS + len(" — ") + _FACT_CHARS


def test_the_answer_side_stays_bounded_too() -> None:
    """It always was. Pinned here so a change to one side does not quietly unbound the other."""
    fact, _ = _remember("do the thing", answer="y" * 900).facts[0]

    assert "y" * (_FACT_CHARS + 1) not in fact


def test_a_short_task_is_left_alone() -> None:
    """Most tasks are already short, and the fix must not turn those into something else."""
    fact, _ = _remember("crie um arquivo OI.txt com a palavra oi").facts[0]

    assert fact.startswith("Accomplished: crie um arquivo OI.txt com a palavra oi")


# --- what must NOT change -------------------------------------------------------------------------


def test_two_briefs_that_open_the_same_way_stay_two_facts() -> None:
    """The key is what dedups, and four real briefs opened with "Leia BRIEF.md e construa…".

    Keying on the shortened head would fold them into one entry that overwrites itself — the same
    project's memory silently replacing another's, which is worse than the size it saves.
    """
    a = _remember("Leia BRIEF.md e construa a pagina.\n\nDetalhes A.").facts[0][1]
    b = _remember("Leia BRIEF.md e construa a pagina.\n\nDetalhes B, bem diferentes.").facts[0][1]

    assert a != b


def test_the_same_task_still_updates_one_entry() -> None:
    """The other half of the same property: re-solving one task must not accumulate entries."""
    a = _remember(BRIEFING).facts[0][1]
    b = _remember(BRIEFING).facts[0][1]

    assert a == b


def test_an_answer_that_is_only_whitespace_leaves_the_task_alone() -> None:
    fact, _ = _remember("do the thing", answer="\n\n   \n").facts[0]

    assert fact == "Accomplished: do the thing"


# --- and the other half of the same screen --------------------------------------------------------


def test_the_skills_endpoint_says_whether_anything_reads_the_cards(tmp_path: Any) -> None:
    """A count of zero means two different things, and the screen was showing the count alone.

    Measured: fourteen cards, all marked ACTIVE, all reading `0 uses`. The available reading is
    that the agent tried them and they were useless; the truth is that retrieval is off by default,
    so nothing consulted them. The flag has to travel with the count or the count misleads.
    """
    from tests.test_api import _client  # noqa: PLC0415

    body = _client(tmp_path).get("/api/skills").json()

    assert "cards_read" in body, "the screen cannot explain why every count is zero"
    assert body["cards_read"] is False, "CHIMERA_SKILL_CARDS is off by default and this must say so"


def test_the_flag_follows_the_setting_rather_than_being_hardcoded(tmp_path: Any) -> None:
    """The half a constant would pass: a field pinned to one value is not a report of anything."""
    import os  # noqa: PLC0415

    from tests.test_api import _client  # noqa: PLC0415

    os.environ["CHIMERA_SKILL_CARDS"] = "1"
    try:
        body = _client(tmp_path).get("/api/skills").json()
    finally:
        os.environ.pop("CHIMERA_SKILL_CARDS", None)

    assert body["cards_read"] is True
