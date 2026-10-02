"""What the harness writes around a task must not decide which skills the model is shown.

Study 28, P7. ``AutonomousAgent._compose`` heads every autonomous prompt with ``Task:``, and skill
retrieval matched on ONE shared word. ``data_analysis`` describes itself as "a data task", so every
cron job, ``solve`` and ``/api/runs`` run was handed the data-analysis skill, whatever it was about:
"Resuma o estado do PassaPro e poste no #geral" included. Nothing failed; the block just sat in the
prompt telling the model that pandas and scikit-learn fit the job. Measured on 26 realistic tasks:
26 of 26 composed prompts got ``data_analysis`` before, 3 of 26 after, and those three are the data
tasks.

The same file covers the other half of P7: the ``echo`` tool, a test/demo tool that every surface
offered the model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.autonomous import AutonomousAgent
from chimera.core.planner import Plan
from chimera.skills import default_registry as default_skill_registry
from chimera.skills import retrieve_relevant_skills
from chimera.skills.retrieval import MIN_SHARED_WORDS, _tokenize
from chimera.tools.builtin import OPTIONAL_TOOLS, EchoTool
from chimera.tools.builtin import default_registry as default_tool_registry
from chimera.tools.registry import ToolRegistry


def _retrieved(query: str) -> list[str]:
    return [skill.name for skill in retrieve_relevant_skills(default_skill_registry(), query)]


def _as_the_runner_sends_it(task: str) -> str:
    return AutonomousAgent._compose(task, None, "", "")


@pytest.mark.parametrize(
    "task",
    [
        "Resuma o estado do PassaPro e poste no #geral.",
        "Check the eToro portfolio and post a short summary of today's P&L.",
        "Refactor the cache module so the eviction policy is configurable.",
        "What is the capital of Australia?",
    ],
)
def test_a_task_prefixed_task_with_no_data_topic_retrieves_no_skill(task: str) -> None:
    composed = _as_the_runner_sends_it(task)
    assert composed.startswith("Task: ")  # the premise: this is the string retrieval is handed
    assert _retrieved(composed) == []


@pytest.mark.parametrize(
    "task",
    [
        "Load the iris dataset, train a classifier and report its accuracy.",
        "Compute summary statistics for sales.csv and train a regression model on it.",
    ],
)
def test_a_real_data_task_still_retrieves_data_analysis(task: str) -> None:
    assert _retrieved(task)[:1] == ["data_analysis"]
    assert _retrieved(_as_the_runner_sends_it(task))[:1] == ["data_analysis"]


def test_the_runners_own_scaffolding_tokenizes_to_nothing() -> None:
    """The pin between the stoplist and ``_compose``: every header it can write, with no task in it.

    If ``_compose`` gains a header word, this fails here rather than that word quietly matching
    some skill on every autonomous run, which is how "Task:" went unnoticed.
    """
    scaffolding = AutonomousAgent._compose("", Plan(steps=["-"]), "", "-")
    assert "Task:" in scaffolding and "Plan:" in scaffolding and "Feedback" in scaffolding
    assert _tokenize(scaffolding) == set()


def test_one_shared_word_is_a_coincidence_not_a_match() -> None:
    # "text" is the one word "Translate this text into Spanish" shares with the echo skill, and the
    # Portuguese "data" (a date) the one "Qual a data de hoje?" shares with both data skills.
    assert MIN_SHARED_WORDS == 2
    assert _retrieved("Translate this text into Spanish.") == []
    assert _retrieved("Qual a data de hoje?") == []


def test_the_descriptions_own_how_to_verbs_are_not_a_topic() -> None:
    # The data skills tell the model to "write runnable Python ... run the result". With two
    # shared words required, those verbs alone were still two: "Run the tests and report the
    # result" got both data skills. These two tasks are from the same set the stoplist was read
    # off, so this pins the stoplist; it is not independent evidence that it generalizes.
    assert _retrieved("Run the tests and report the result.") == []
    assert _retrieved(_as_the_runner_sends_it("Write a haiku about autumn.")) == []


def test_an_accented_word_stays_one_word() -> None:
    # The old ASCII pattern cut "análise" into "an" + "lise" and "regressão" into "regress" + "o":
    # fragments that can match an English description by accident.
    assert _tokenize("análise de regressão") == {"análise", "regressão"}


def test_echo_is_not_in_the_default_registry_but_still_works_when_registered(tmp_path: Path) -> None:
    assert "echo" not in default_tool_registry(tmp_path)
    registry = ToolRegistry()
    registry.register(EchoTool())
    assert registry.get("echo").run(text="still works") == "still works"
    # Opt-in, so the description guards treat it like the other sometimes-present tools and its
    # translated description (a key nobody may delete) stays legitimate.
    assert "echo" in OPTIONAL_TOOLS
