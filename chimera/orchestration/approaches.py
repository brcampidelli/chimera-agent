"""Ready-made approaches for a crew, chosen so that two of them produce different diffs.

Every worker in an :class:`~chimera.orchestration.crew.IsolatedCrew` attacks the SAME task in
its own checkout, and the merge rule is mechanical one-file-one-owner: a file two *successful*
workers both changed is a conflict and lands from NEITHER of them
(:func:`~chimera.orchestration.isolation._merge_back`). Read that twice, because it inverts the
intuition — the more workers pass the check, the less of their work survives.

So a crew is not "more attempts, more chance one works". It is a *competitive* mechanism, and it
pays only when the check DISCRIMINATES: several attempts, most eliminated, ideally one survivor
per file. Two workers running the same approach are the pathological case — near-identical
diffs, both passing, both discarded.

Which is what this catalogue is for. These are not personalities; picking "optimistic" and
"pessimistic" would be theatre, because both write the same code. Each entry here changes
something structural about the diff that comes out: how many files it touches, whether it edits
tests, whether it reaches outside the standard library, whether it rewrites or patches. That is
the axis along which a check can actually tell them apart.

The instruction text is a system prompt sent to a model, so it lives here in English with the
rest of the repo's prompts (``WORKER_SYSTEM``, ``_DECOMPOSE_SYSTEM``) rather than in the UI's
translation table — a translated prompt is a different prompt, and the CLI needs to reach these
too. What the screen translates is the label and the one-line description, keyed by ``id``.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["APPROACHES", "CrewApproach", "approach", "default_pair"]


@dataclass(frozen=True)
class CrewApproach:
    """One way of attacking a task, and the instruction that produces it."""

    id: str
    instruction: str


APPROACHES: tuple[CrewApproach, ...] = (
    CrewApproach(
        "minimal",
        "Make the SMALLEST change that solves the task. Touch as few files and as few lines as "
        "you can. Do not refactor, rename, reformat or tidy anything you were not asked about. "
        "If a one-line change is enough, make the one-line change.",
    ),
    CrewApproach(
        "rewrite",
        "Rewrite the unit that owns this problem so it is correct by construction, rather than "
        "patching around the symptom. Keep the public interface, replace the body. Prefer clear "
        "structure over a small diff.",
    ),
    CrewApproach(
        "test_first",
        "Write a failing test that reproduces the problem FIRST, and only then the code that "
        "makes it pass. Leave both. If a test for this already exists, extend it to cover the "
        "case that is broken.",
    ),
    CrewApproach(
        "defensive",
        "Handle the failure paths explicitly: invalid input, empty and boundary values, and the "
        "errors the code you call can raise. Validate at the entry point and fail with a message "
        "that says what was wrong. Do not swallow exceptions.",
    ),
    CrewApproach(
        "no_new_deps",
        "Solve this with what the project already imports and the standard library only. Add no "
        "dependency, no new package, no new import of a third-party module. If that makes the "
        "solution longer, write the longer solution.",
    ),
    CrewApproach(
        "follow_local",
        "Read the code around this first — its neighbours in the same file and the files it "
        "imports — and solve the task the way this codebase already solves that kind of problem. "
        "Match the existing naming, error handling and structure, even where you would have "
        "chosen differently.",
    ),
)

_BY_ID = {item.id: item for item in APPROACHES}


def approach(approach_id: str) -> CrewApproach | None:
    """The approach with this id, or ``None`` — callers decide what an unknown id means."""
    return _BY_ID.get(approach_id)


def default_pair() -> tuple[CrewApproach, CrewApproach]:
    """The two approaches a crew starts with.

    Deliberately the widest pair in the catalogue: the smallest possible edit against a clean
    rewrite. They disagree about how much of the file to touch, which is the disagreement most
    likely to leave exactly one of them standing after a check.
    """
    return _BY_ID["minimal"], _BY_ID["rewrite"]
