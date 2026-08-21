"""The catalogue a crew is assembled from, and the rule that makes it worth having.

A crew's value does not come from running more attempts. Every worker attacks the SAME task and
the merge rule is one-file-one-owner, so two workers who both succeed on one file both lose it.
More passing workers means less landed work — which means the catalogue's job is to make the
attempts *separable*, and these tests are about that property rather than about the prose.
"""

from __future__ import annotations

from chimera.orchestration.approaches import APPROACHES, approach, default_pair


def test_no_two_approaches_say_the_same_thing() -> None:
    instructions = [item.instruction for item in APPROACHES]

    # Two entries with the same instruction would be the failure this catalogue exists to
    # prevent, shipped as a feature: identical diffs, both passing, both discarded.
    assert len(set(instructions)) == len(instructions)
    assert len({item.id for item in APPROACHES}) == len(APPROACHES)


def test_every_approach_actually_instructs() -> None:
    for item in APPROACHES:
        # The instruction is the whole of an approach — an empty one is a worker with no brief,
        # which is the blank-box default this replaced.
        assert len(item.instruction.strip()) > 40, item.id


def test_the_default_pair_disagrees_about_how_much_to_touch() -> None:
    first, second = default_pair()

    # Not any two entries: the pair a crew starts with has to be the one most likely to leave
    # exactly ONE worker standing. Smallest-possible-edit against clean-rewrite disagree about
    # the size of the diff, which is what a check can separate.
    assert first.id != second.id
    assert {first.id, second.id} == {"minimal", "rewrite"}


def test_an_unknown_id_is_absent_rather_than_invented() -> None:
    # Callers decide what a missing approach means; a fabricated default would silently give a
    # worker an instruction nobody chose.
    assert approach("no-such-approach") is None
    assert approach("minimal") is not None
