"""What the reviewer may hold the work to, and what it may not.

Found by using the installed rc37. A run whose own diff proves `README.md` was written was failed
with this feedback, and verify-or-revert then deleted the file:

    "O arquivo README.md deve existir e conter uma linha descrevendo o projeto, mas a localização
     do projeto no caminho 'Desktop/teste-chimera/site-institucional' não foi incluída na descrição."

The agreed requirement was *"o README.md deve existir"*. That path appears in neither the
requirement nor the answer — it exists only as a globally-scoped memory fact, about a different
project entirely.

**Which gate wrote it is decidable from the evidence.** `RequirementChecklist.grade` builds its
prompt from the requirement list and the answer and nothing else — it never sees the path. The
reviewer receives `context`, and `context` contained the recalled facts. Only one gate could have
produced that sentence.

So: the worker's context and the reviewer's were the same string, and that made every advisory
thing enforceable. `requirements_ctx` stays, because it IS the agreement — a checklist somebody
read and edited before the run. Recalled facts, distilled lessons, skill cards, the playbook, the
repository map and file bodies are material for the worker to think with, not a contract.
"""

from __future__ import annotations

import inspect

from chimera.core.autonomous import AutonomousAgent


def _run_source() -> str:
    return inspect.getsource(AutonomousAgent.run)


def test_the_reviewer_is_given_the_judge_context() -> None:
    """Every review call, not most of them. There are three — a passing attempt's PROBE proxy, the
    verifier-failed path and the no-verifier path — and one left on the worker's context is a leak
    that only shows up on whichever branch nobody exercised."""
    source = _run_source()
    # `attempt_judge_context` is `judge_context` plus what THIS attempt changed on disk — the
    # agreement, plus evidence about the answer. The name changed when the diff was added; the
    # property did not, and the assertion that matters is the negative one below.
    assert source.count("self._review(task, answer, attempt_judge_context)") == 3
    assert "self._review(task, answer, context)" not in source
    assert "self._review(task, answer, judge_context)" not in source


def test_the_judge_context_is_the_agreement_and_nothing_else() -> None:
    source = _run_source()
    assert "judge_context = requirements_ctx" in source


def test_what_the_attempt_changed_is_added_as_evidence_not_as_a_criterion() -> None:
    """The one thing allowed to join the agreement, and why it is not a widening of it.

    A recalled fact from another project can make something enforceable that nobody agreed to —
    that is the leak this file exists for. A diff cannot: it describes what the answer is a claim
    ABOUT, so it can only stop the reviewer being wrong about whether the work happened. Which it
    was, on the shipped build, five times out of five, while the receipt beside it read
    ``diff_productive: true``.
    """
    source = _run_source()
    assert "attempt_judge_context = (" in source
    line = source[source.index("attempt_judge_context = (") :][:400]
    assert "judge_context" in line and "evidence_ctx" in line
    for advisory in ("facts_ctx", "card_ctx", "playbook_ctx", "repo_ctx", "lessons"):
        assert advisory not in line, f"{advisory} rejoined the judge through the evidence line"


def test_the_worker_still_gets_everything() -> None:
    """The control, and the half that could have been broken by fixing the other. Narrowing what
    the JUDGE sees must not narrow what the worker can think with — recalled facts and learned
    cards are why the loop improves at all."""
    source = _run_source()
    assert "(spine, repo_ctx, lessons, card_ctx, facts_ctx, playbook_ctx, requirements_ctx)" in source


def test_recalled_facts_never_reach_the_judge() -> None:
    """The specific leak, named. `facts_ctx` is memory recall — including facts scoped to every
    project — and a judge holding work to a fact recalled from somewhere else is a judge enforcing
    a criterion nobody agreed to."""
    source = _run_source()
    judge_line = next(ln for ln in source.splitlines() if "judge_context =" in ln)
    for advisory in ("facts_ctx", "lessons", "card_ctx", "playbook_ctx", "repo_ctx", "spine"):
        assert advisory not in judge_line, f"{advisory} became enforceable again"


def test_the_checklist_grader_could_not_have_written_that_feedback() -> None:
    """Pinned because it is what makes the diagnosis a conclusion rather than a guess: the grader's
    prompt is built from the requirement list and the answer, so a path present in neither could
    not have come from it. If this prompt ever grows a context argument, the elimination above
    stops holding and this file's reasoning needs redoing."""
    from chimera.core.checklist import RequirementChecklist

    grade = inspect.getsource(RequirementChecklist.grade)
    assert 'prompt = f"Requirements:\\n{listing}\\n\\n<<answer>>\\n{answer}\\n<<end-answer>>"' in grade
    # `task` is a parameter it accepts and deliberately does not put in the prompt.
    assert "{task}" not in grade
