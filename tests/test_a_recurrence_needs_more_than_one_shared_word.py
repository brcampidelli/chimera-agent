"""A recurrence is the same work, not a shared word.

``ExperienceBuffer.relevant`` ranks by any token overlap. That is right for planning ("show me
anything related") and wrong for minting: the autonomous loop counts prior successes and failures
through ``recurring`` to decide whether a pattern recurred enough to become a card. A single shared
word, a shared stopword, or a shared fragment of an accented word must not count.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.autonomous import AutonomousAgent
from chimera.evolution.experience import ExperienceBuffer


def _agent(buffer: ExperienceBuffer) -> Any:
    # The two counters read one attribute; a bare instance carries exactly that, as the other
    # minting tests in this repository do.
    agent = object.__new__(AutonomousAgent)
    agent.experience = buffer
    return agent


def test_tasks_that_share_only_a_stopword_do_not_recur(tmp_path: Path) -> None:
    buffer = ExperienceBuffer(tmp_path / "experience.json")
    buffer.record("rename the config parser", "success")

    assert AutonomousAgent._count_prior_successes(_agent(buffer), "fix the loader") == 0


def test_tasks_that_share_one_content_word_do_not_recur(tmp_path: Path) -> None:
    buffer = ExperienceBuffer(tmp_path / "experience.json")
    buffer.record("rename the loader module", "failure")

    assert AutonomousAgent._count_prior_failures(_agent(buffer), "fix the loader") == 0


def test_a_single_word_task_does_not_recur_with_a_longer_one(tmp_path: Path) -> None:
    """"fix the loader" against "loader": one content word each side, and it is the only word."""
    buffer = ExperienceBuffer(tmp_path / "experience.json")
    buffer.record("loader", "success")

    assert AutonomousAgent._count_prior_successes(_agent(buffer), "fix the loader") == 0


def test_tasks_that_name_the_same_work_do_recur(tmp_path: Path) -> None:
    buffer = ExperienceBuffer(tmp_path / "experience.json")
    buffer.record("fix the failing test in the loader module", "success")
    buffer.record("fix the failing test in the loader", "failure")

    agent = _agent(buffer)
    assert AutonomousAgent._count_prior_successes(agent, "fix the failing test in the loader") == 1
    assert AutonomousAgent._count_prior_failures(agent, "fix the failing test in the loader") == 1


def test_portuguese_work_with_two_shared_words_recurs(tmp_path: Path) -> None:
    buffer = ExperienceBuffer(tmp_path / "experience.json")
    buffer.record("Corrigir o carregador do módulo", "success")

    assert AutonomousAgent._count_prior_successes(_agent(buffer), "corrigir carregador do modulo") == 1


def test_an_accented_word_does_not_split_into_fragments(tmp_path: Path) -> None:
    """"ações" and "funções" must not both reduce to the fragment "es", which they share.

    With an ASCII-only tokenizer they share "es" and nothing else, so the pair scored two shared
    tokens and was counted as the same work. Tokenizing whole Unicode words keeps them apart.
    """
    buffer = ExperienceBuffer(tmp_path / "experience.json")
    buffer.record("ver ações", "success")

    assert AutonomousAgent._count_prior_successes(_agent(buffer), "ver funções") == 0


def test_an_accent_is_folded_so_the_same_word_matches(tmp_path: Path) -> None:
    buffer = ExperienceBuffer(tmp_path / "experience.json")
    buffer.record("ver ações pendentes", "success")

    assert AutonomousAgent._count_prior_successes(_agent(buffer), "ver acoes pendentes") == 1
