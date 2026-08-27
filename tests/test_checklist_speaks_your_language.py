"""The requirement checklist has to come back in the language the task was written in.

Found by using the installed rc37: asked in Portuguese for *"faca uma pagina de contato com
formulario e telefone"*, the list came back as *"create a contact page"*, *"include a form"*,
*"include a phone number"*.

That list is the whole product of the feature. It exists so somebody reads it and corrects it
before the run, and whatever they add becomes an acceptance criterion — a checklist a person cannot
read is a checklist they cannot correct.

Measured against the real model, same request, five repeats each:

    old prompt   5 of 5 in English
    new prompt   0 of 5

**A single sample nearly buried this.** The first paired run drew Portuguese from the old prompt
and I doubted the finding; five repeats showed that draw was luck. Two samples do not estimate
variance — they produce a difference.

The spec drafter shipped in the same wave already said "write it in the same language as the
request" and behaved correctly throughout. Same job, two prompts, one of them told.
"""

from __future__ import annotations

from chimera.core.checklist import _EXTRACT_SYSTEM, _GRADE_SYSTEM


def test_the_extraction_is_told_to_answer_in_the_task_s_language() -> None:
    assert "SAME LANGUAGE" in _EXTRACT_SYSTEM


def test_it_says_why_rather_than_only_what() -> None:
    """A rule with its reason survives an edit that a bare instruction does not: the next person to
    shorten this prompt can see what the sentence is buying."""
    assert "cannot correct" in _EXTRACT_SYSTEM


def test_the_two_prompts_of_this_feature_agree() -> None:
    """The drafter and the checklist do the same job on the same screen. One of them having the
    rule and the other not is how a user gets half a feature in their own language."""
    from chimera.orchestration.draft import _SYSTEM as DRAFTER

    assert "same language as the request" in DRAFTER
    assert "SAME LANGUAGE" in _EXTRACT_SYSTEM


def test_the_grader_is_not_asked_to_translate() -> None:
    """The control, and it is the half that could have been broken by fixing the other. The grader
    receives requirement texts and returns them with a verdict; telling it to write in the task's
    language would invite it to REPHRASE what it echoes, and the caller matches those strings to
    decide what was missed."""
    assert "SAME LANGUAGE" not in _GRADE_SYSTEM
    assert "one entry per" in _GRADE_SYSTEM


def test_the_instruction_costs_one_sentence() -> None:
    """This prompt runs on every checklist extraction. Asserted rather than assumed: a prompt
    nobody measures grows a sentence at a time until somebody finds it in a token bill."""
    assert len(_EXTRACT_SYSTEM) < 1200, f"{len(_EXTRACT_SYSTEM)} characters, sent every extraction"
