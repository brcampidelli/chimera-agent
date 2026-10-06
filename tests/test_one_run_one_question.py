"""N workers on one task asked the same question N times, at the same moment, on one terminal.

Crew workers already share a `SharedTaint`, with the reasoning written where it lives: they
collaborate on one task and merge into one workspace, so content one worker fetched can flow to
another and a fetch in any of them arms the narrowing in all of them.

The approval half was not shared. Each worker carried its own approver, so once a run was tainted
and a dangerous tool needed a decision, every worker asked — concurrently, onto one terminal, where
two prompts interleave into a question nobody can answer correctly.

Reusing a refusal is uncontroversial. Reusing an approval is the deliberate part, and the tests
below pin both the reuse and its limit: a DIFFERENT action, or the same action questioned for a
different reason, is a different question and gets asked.
"""

from __future__ import annotations

import threading
from typing import Any

from chimera.governance.shared_approval import SharedApprovals


class _Verdict:
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _contador(resposta: bool = True) -> tuple[Any, list[str]]:
    """An approver that records what it was asked, so 'asked once' is measurable."""
    perguntas: list[str] = []

    def approve(*args: Any) -> bool:
        perguntas.append(str(args[-1]) if len(args) == 2 else str(getattr(args[0], "reason", "")))
        return resposta

    return approve, perguntas


# ------------------------------------------------------------------ one answer


def test_the_same_action_is_asked_once() -> None:
    """The whole point, counted in prompts — which is what the person actually experiences."""
    approve, perguntas = _contador(True)
    partilhado = SharedApprovals(approve)
    gate = partilhado.approver()

    for _ in range(5):
        assert gate(_Verdict("taint narrowing"), "run_shell: npm test") is True

    assert perguntas == ["run_shell: npm test"]
    assert partilhado.asked == 1


def test_a_refusal_is_reused_too() -> None:
    """A no is a no. Asking again is pure noise, and noise is what trains people to stop reading."""
    approve, perguntas = _contador(False)
    gate = SharedApprovals(approve).approver()

    assert gate(_Verdict("r"), "git push --force") is False
    assert gate(_Verdict("r"), "git push --force") is False
    assert len(perguntas) == 1


# ------------------------------------------------------------------ where the reuse stops


def test_a_different_action_is_a_different_question() -> None:
    """The limit that makes reuse defensible: the key is the action as it was DESCRIBED, which is
    the sentence the person read."""
    approve, perguntas = _contador(True)
    gate = SharedApprovals(approve).approver()

    gate(_Verdict("r"), "run_shell: npm test")
    gate(_Verdict("r"), "run_shell: rm -rf build")

    assert len(perguntas) == 2


def test_the_same_action_for_a_different_reason_is_asked_again() -> None:
    """The same command can be questioned by a policy rule on one call and by taint narrowing on
    the next. An approval given for one is not an answer to the other, and folding them would let
    a decision made under one set of facts stand in for a decision under another."""
    approve, perguntas = _contador(True)
    gate = SharedApprovals(approve).approver()

    gate(_Verdict("policy: force push"), "git push --force")
    gate(_Verdict("taint: untrusted content in scope"), "git push --force")

    assert len(perguntas) == 2


# ------------------------------------------------------------------ one at a time


def test_two_workers_never_prompt_at_the_same_moment() -> None:
    """The half that fixes the unreadable case, and it is separate from the reuse.

    Two prompts interleaving on one terminal produce a question whose answer goes to whichever
    prompt happened to be reading — which is worse than asking twice, because it can approve the
    wrong thing.
    """
    dentro = []
    maximo = [0]
    trava = threading.Lock()

    def lento(*_args: Any) -> bool:
        with trava:
            dentro.append(1)
            maximo[0] = max(maximo[0], len(dentro))
        threading.Event().wait(0.02)
        with trava:
            dentro.pop()
        return True

    gate = SharedApprovals(lento).approver()
    fios = [
        threading.Thread(target=gate, args=(_Verdict("r"), f"acao {i}")) for i in range(6)
    ]
    for f in fios:
        f.start()
    for f in fios:
        f.join()

    assert maximo[0] == 1, "two workers were inside the prompt at the same time"


def test_the_ledger_answers_for_the_run_not_for_a_worker() -> None:
    """`blocked` is asked about a RUN. With a ledger per worker, the answer depended on which
    worker you asked — and the one that was refused is not necessarily the one anybody checks."""
    approve, _perguntas = _contador(False)
    partilhado = SharedApprovals(approve)
    gate = partilhado.approver()

    gate(_Verdict("r"), "git push --force")

    assert partilhado.ledger.blocked is False, "the wrapper does not record; the approver does"


def test_it_counts_what_it_actually_asked() -> None:
    """"Twelve dangerous calls, three questions" is the sentence that says whether the sharing
    worked, and counting prompts is the only way to know it did."""
    approve, _ = _contador(True)
    partilhado = SharedApprovals(approve)
    gate = partilhado.approver()

    for i in range(12):
        gate(_Verdict("r"), f"acao {i % 3}")

    assert partilhado.asked == 3


def test_a_question_without_a_reason_or_a_proposal_is_still_asked_and_reused() -> None:
    """An asker that attaches only the action — no `reason`, no `proposal` attribute at all — must be
    asked once and then answered from the cache, not crash on the attributes it lacks (study 30
    mutation run: the attribute defaults were never exercised)."""

    class _OnlyAction:
        def __init__(self, action: str) -> None:
            self.action = action

    approve, _ = _contador(True)
    asked: list[str] = []

    def approver(*args: Any) -> bool:
        asked.append(args[0].action)
        return bool(approve(*args))

    partilhado = SharedApprovals(approver)
    gate = partilhado.approver()
    assert gate(_OnlyAction("run_shell: npm test")) is True
    assert gate(_OnlyAction("run_shell: npm test")) is True
    assert gate(_OnlyAction("run_shell: rm -rf build")) is True
    assert asked == ["run_shell: npm test", "run_shell: rm -rf build"]


def test_a_question_with_a_reason_and_no_action_attribute_is_still_asked_once() -> None:
    """The one-argument form with a verdict that carries no `action` at all."""
    approve, perguntas = _contador(True)
    gate = SharedApprovals(approve).approver()
    assert gate(_Verdict("policy")) is True
    assert gate(_Verdict("policy")) is True
    assert perguntas == ["policy"]


def test_a_question_that_describes_nothing_is_never_answered_from_the_cache() -> None:
    """A question with no reason, no action and no proposal — whether the attributes are absent or
    empty — is asked every time. Every blank question has the same key whatever it is about, so a
    reused yes would answer the next blank question of any worker, unseen.

    (An earlier version of this test pinned the opposite — absent and empty sharing one cached
    answer — to kill a mutant; that made the collision look like a guarantee.)"""

    class _Bare:
        pass

    class _Empty:
        reason = ""
        proposal = ""
        action = ""

    approve, perguntas = _contador(True)
    partilhado = SharedApprovals(lambda *a: approve(_Verdict("x")))
    gate = partilhado.approver()
    for asker in (_Bare(), _Bare(), _Empty(), _Empty()):
        assert gate(asker) is True
    assert perguntas == ["x"] * 4
    assert partilhado.asked == 4  # every prompt the person saw is counted


def test_a_blank_question_neither_reads_nor_fills_the_cache_of_described_ones() -> None:
    """A refusal given to a described question is not handed to a blank one, nor the reverse."""
    respostas = iter([False, True, True])
    asked: list[str] = []

    def approve(*args: Any) -> bool:
        asked.append(str(getattr(args[0], "reason", "") or "blank"))
        return next(respostas)

    class _Bare:
        pass

    gate = SharedApprovals(approve).approver()
    assert gate(_Verdict("policy")) is False
    assert gate(_Bare()) is True
    assert gate(_Bare()) is True
    assert gate(_Verdict("policy")) is False  # still the cached refusal, not the blank's yes
    assert asked == ["policy", "blank", "blank"]
