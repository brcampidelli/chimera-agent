"""Without a terminal, every REVIEW became a refusal and the three-state gate collapsed into two.

`Approver` is a synchronous bool and the only implementation that asks anybody calls `input()`. On
the VPS, in a container, under cron, `approver_for("ask")` degrades to `deny` — which is the right
default, and also means the mandate that says "confirm before billing, before a destructive
migration, before touching RLS" has nothing to confirm with.

The three pieces of an answer were already here and had never been composed: a durable pause with a
resume key, a delivery channel that reaches a person, and the approver seam. This is the middle.

Every test below is about one of three properties, because they are what decide whether an
unattended gate is safe to have at all: silence refuses, an answer belongs to one question, and
nothing is remembered across runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.governance.pending import (
    STALE_SECONDS,
    answer,
    ask_durably,
    pending,
    sweep,
)


class _Relogio:
    """A clock and a sleep that advance together, so a 15-minute wait costs no wall time."""

    def __init__(self) -> None:
        self.agora = 0.0

    def __call__(self) -> float:
        return self.agora

    def sleep(self, segundos: float) -> None:
        self.agora += segundos


# ------------------------------------------------------------------ silence refuses


def test_an_unanswered_question_is_refused(tmp_path: Path) -> None:
    """The property everything else rests on. A gate that reads silence as consent produces a
    record of an approval nobody gave, which is worse than having no gate."""
    relogio = _Relogio()

    assert ask_durably(
        tmp_path, "git push --force", "policy: force push",
        wait_seconds=60, poll_seconds=5, clock=relogio, sleep=relogio.sleep,
    ) is False


def test_it_does_not_wait_forever(tmp_path: Path) -> None:
    """A worker thread held for an afternoon by a question nobody will read is not a safer outcome
    than a refusal — it is the same refusal with the run stuck behind it."""
    relogio = _Relogio()

    ask_durably(
        tmp_path, "x", "y", wait_seconds=100, poll_seconds=10, clock=relogio, sleep=relogio.sleep
    )

    assert relogio.agora <= 110


def test_a_question_that_cannot_be_written_refuses_immediately(tmp_path: Path) -> None:
    """Nobody will answer a question that was never asked, and waiting fifteen minutes to reach the
    same refusal parks a worker for nothing."""
    ocupado = tmp_path / "home"
    ocupado.write_text("sou um arquivo, nao uma pasta", encoding="utf-8")
    relogio = _Relogio()

    assert ask_durably(
        ocupado, "x", "y", wait_seconds=600, clock=relogio, sleep=relogio.sleep
    ) is False
    assert relogio.agora == 0.0, "it waited for an answer to a question it never recorded"


# ------------------------------------------------------------------ an answer arrives


def test_a_yes_is_a_yes(tmp_path: Path) -> None:
    relogio = _Relogio()
    respondido: list[bool] = []

    def responde(_texto: str) -> None:
        # Answer as soon as the question is delivered — which is what a person with the chat open
        # does, and what makes this a test of the round trip rather than of the timeout.
        pedido = pending(tmp_path)[0]
        respondido.append(answer(tmp_path, pedido.id, True))

    assert ask_durably(
        tmp_path, "npm test", "taint narrowing", deliver=responde,
        wait_seconds=60, poll_seconds=1, clock=relogio, sleep=relogio.sleep,
    ) is True
    assert respondido == [True]


def test_a_no_is_a_no(tmp_path: Path) -> None:
    relogio = _Relogio()

    def responde(_texto: str) -> None:
        answer(tmp_path, pending(tmp_path)[0].id, False)

    assert ask_durably(
        tmp_path, "rm -rf /", "policy", deliver=responde,
        wait_seconds=60, poll_seconds=1, clock=relogio, sleep=relogio.sleep,
    ) is False


def test_an_unreadable_answer_is_a_refusal(tmp_path: Path) -> None:
    """Same rule as silence. An answer file that does not parse tells us nothing, and "nothing" has
    exactly one safe reading."""
    relogio = _Relogio()

    def responde(_texto: str) -> None:
        pedido = pending(tmp_path)[0]
        (tmp_path / "approvals" / f"{pedido.id}.answer.json").write_text("{lixo", encoding="utf-8")

    assert ask_durably(
        tmp_path, "x", "y", deliver=responde,
        wait_seconds=60, poll_seconds=1, clock=relogio, sleep=relogio.sleep,
    ) is False


def test_a_failed_delivery_does_not_fail_the_run(tmp_path: Path) -> None:
    """The question is still on disk and `chimera approve` can still find it — a chat outage must
    not turn into a crash inside a tool call."""
    relogio = _Relogio()

    def quebra(_texto: str) -> None:
        raise RuntimeError("webhook fora do ar")

    assert ask_durably(
        tmp_path, "x", "y", deliver=quebra, wait_seconds=10, poll_seconds=5,
        clock=relogio, sleep=relogio.sleep,
    ) is False


# ------------------------------------------------------------------ an answer is for one question


def test_answering_an_id_nobody_asked_about_fails(tmp_path: Path) -> None:
    """Otherwise a mistyped id writes an answer that the NEXT question picks up."""
    (tmp_path / "approvals").mkdir()

    assert answer(tmp_path, "inventado", True) is False


def test_the_question_carries_what_it_is_about(tmp_path: Path) -> None:
    """A person reading a chat message needs the action and the reason — "review required" alone is
    a request to approve something unspecified, which nobody can do responsibly."""
    entregue: list[str] = []
    relogio = _Relogio()

    ask_durably(
        tmp_path, "git push --force origin main", "policy: force push",
        deliver=entregue.append, wait_seconds=5, poll_seconds=5,
        clock=relogio, sleep=relogio.sleep,
    )

    assert "git push --force origin main" in entregue[0]
    assert "force push" in entregue[0]
    assert "chimera approve" in entregue[0], "it must say how to answer"


# ------------------------------------------------------------------ nothing survives the run


def test_a_finished_question_leaves_nothing_behind(tmp_path: Path) -> None:
    """Reusing yesterday's yes for today's question is the same defect as treating silence as
    consent, one day later."""
    relogio = _Relogio()

    def responde(_texto: str) -> None:
        answer(tmp_path, pending(tmp_path)[0].id, True)

    ask_durably(
        tmp_path, "x", "y", deliver=responde, wait_seconds=60, poll_seconds=1,
        clock=relogio, sleep=relogio.sleep,
    )

    assert pending(tmp_path) == []
    assert list((tmp_path / "approvals").glob("*.json")) == []


def test_a_stale_question_is_swept(tmp_path: Path) -> None:
    """A directory of dead questions makes `chimera approve` unreadable and hides the live one."""
    directory = tmp_path / "approvals"
    directory.mkdir()
    velho = directory / "velho.ask.json"
    velho.write_text(json.dumps({"id": "velho", "action": "a", "reason": "r"}), encoding="utf-8")
    import os

    antigo = velho.stat().st_mtime - STALE_SECONDS - 60
    os.utime(velho, (antigo, antigo))

    assert sweep(tmp_path) == 1
    assert pending(tmp_path) == []


def test_a_live_question_is_not_swept(tmp_path: Path) -> None:
    """The guard against a sweep that clears the question somebody is about to answer."""
    directory = tmp_path / "approvals"
    directory.mkdir()
    (directory / "novo.ask.json").write_text(
        json.dumps({"id": "novo", "action": "a", "reason": "r"}), encoding="utf-8"
    )

    assert sweep(tmp_path) == 0
    assert [p.id for p in pending(tmp_path)] == ["novo"]


# ------------------------------------------------------------------ it is actually wired


class _SemTerminal:
    """stdin with no tty — the VPS, a container, cron."""

    def isatty(self) -> bool:
        return False


def test_without_a_terminal_and_with_a_home_it_asks(
    tmp_path: Path, monkeypatch: object
) -> None:
    """The wiring, and the whole point of the module.

    `approver_for("ask")` degraded to `deny` with no tty, which is the right default and is exactly
    why the three-state gate had two states on every unattended surface. With a home it asks
    instead — and a sabotage that removed this branch passed every other test in the file, because
    the mechanism works perfectly whether or not anything calls it.
    """
    import sys as _sys

    from chimera.governance.approval import ApprovalLedger, approver_for

    monkeypatch.setattr(_sys, "stdin", _SemTerminal())  # type: ignore[attr-defined]
    livro = ApprovalLedger()
    entregue: list[str] = []

    def responde(texto: str) -> None:
        entregue.append(texto)
        answer(tmp_path, pending(tmp_path)[0].id, True)

    # Through `approver_for`, not through `ask_elsewhere` — the branch under test is the one that
    # DECIDES to ask. Written the direct way first, and the sabotage that deleted that branch went
    # undetected: the mechanism works perfectly whether or not anything reaches it.
    aprovar = approver_for("ask", livro, home=tmp_path, deliver=responde)

    assert aprovar(_Verdict("policy: force push"), "git push --force") is True
    assert entregue, "nobody was asked"
    assert livro.granted, "the decision was not recorded"


def test_without_a_home_it_still_denies(monkeypatch: object) -> None:
    """The default has to stay refusal. Somebody who has not opted into being asked elsewhere gets
    the behaviour they had, and inventing a channel for them would be the opposite of a default."""
    import sys as _sys

    from chimera.governance.approval import approver_for

    monkeypatch.setattr(_sys, "stdin", _SemTerminal())  # type: ignore[attr-defined]

    assert approver_for("ask")(_Verdict("r"), "git push --force") is False


class _Verdict:
    def __init__(self, reason: str) -> None:
        self.reason = reason
