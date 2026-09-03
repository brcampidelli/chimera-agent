"""The receipt said ``verified: True`` and the delivered tree failed all twenty tests.

Measured on a real run. A task asked for a Python module plus its unittest suite. The receipt read
``verified: True``, ``evidence: verifier``, and carried the verifier's own output:

    Ran 20 tests in 0.001s
    OK

Running the same command against the tree the run left behind: **20 failures out of 20 executions**,
deterministic — not flaky, impossible. The diff the same receipt stored explained it. The verified
version had a line the delivered file did not::

    na versao verificada            no arquivo entregue
      calc.pop("anterior")            calc.pop("anterior")
      calc["anterior"] = anterior     (ausente)
      sha256(calc)                    sha256(calc)   -> nunca bate

Restoring that one line: 20/20 OK. Something wrote after the moment the verdict describes, and
nothing in the receipt could say so, because ``verified`` is a statement about an instant while the
row is read as a statement about the delivery.

These tests do not identify what wrote. That was never established from the artifacts, and a guess
in a receipt is worse than a gap. What they hold is that the claim is now CHECKABLE: a digest of the
tree as verified, a digest at receipt time, and a third state for "we did not look" that is not
allowed to read as "it matched".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.autonomous import Attempt, AutonomousAgent, AutonomousConfig, AutonomousResult
from chimera.core.checkpoint import FileSnapshot, WorkspaceGuard, fingerprint


class _Worker:
    def run(self, *_a: object, **_k: object) -> object:  # pragma: no cover - never called here
        raise AssertionError("these tests never run a task")


def _agente(guard: WorkspaceGuard | None) -> AutonomousAgent:
    a = AutonomousAgent(
        _Worker(), config=AutonomousConfig(use_planner=False, use_manager=False)
    )
    a.guard = guard
    return a


def _resultado(*attempts: Attempt) -> AutonomousResult:
    return AutonomousResult(
        success=any(a.success for a in attempts), answer="", attempts=list(attempts)
    )


def _venceu(impressao: str) -> Attempt:
    a = Attempt(1, "feito", True, True, False, True)
    a.verified_fingerprint = impressao
    return a


# --------------------------------------------------------------------------------------------
# 1. The digest has to notice what the verify command would notice


def test_the_same_tree_has_the_same_digest() -> None:
    a = FileSnapshot(files={"k.py": "x = 1"}, present={"k.py"})
    b = FileSnapshot(files={"k.py": "x = 1"}, present={"k.py"})
    assert fingerprint(a) == fingerprint(b)


def test_one_changed_line_changes_the_digest() -> None:
    """The measured case was exactly this: one line, present in one tree and not the other."""
    antes = FileSnapshot(files={"k.py": 'calc.pop("a")\nsha(calc)'}, present={"k.py"})
    depois = FileSnapshot(files={"k.py": 'calc.pop("a")\ncalc["a"] = a\nsha(calc)'}, present={"k.py"})
    assert fingerprint(antes) != fingerprint(depois)


def test_a_file_appearing_changes_the_digest() -> None:
    """Content-only hashing would call a new file no change, and a new file changes the test run."""
    a = FileSnapshot(files={"k.py": "x = 1"}, present={"k.py"})
    b = FileSnapshot(files={"k.py": "x = 1"}, present={"k.py", "conftest.py"})
    assert fingerprint(a) != fingerprint(b)


def test_a_file_disappearing_changes_the_digest() -> None:
    a = FileSnapshot(files={"k.py": "x = 1", "t.py": "pass"}, present={"k.py", "t.py"})
    b = FileSnapshot(files={"k.py": "x = 1"}, present={"k.py"})
    assert fingerprint(a) != fingerprint(b)


def test_present_but_unread_is_not_the_same_as_present_and_empty() -> None:
    """Binary files land in `present` with no content; hashing both as b"" hides a replacement."""
    binario = FileSnapshot(files={}, present={"logo.png"})
    vazio = FileSnapshot(files={"logo.png": ""}, present={"logo.png"})
    assert fingerprint(binario) != fingerprint(vazio)


def test_the_digest_does_not_depend_on_insertion_order() -> None:
    a = FileSnapshot(files={"a.py": "1", "b.py": "2"}, present={"a.py", "b.py"})
    b = FileSnapshot(files={"b.py": "2", "a.py": "1"}, present={"b.py", "a.py"})
    assert fingerprint(a) == fingerprint(b)


# --------------------------------------------------------------------------------------------
# 2. The reproduction: verified, then written, then reported


def test_a_write_after_the_verdict_is_reported(tmp_path: Path) -> None:
    """The measured failure, in miniature."""
    alvo = tmp_path / "kernel.py"
    alvo.write_text('calc.pop("a")\ncalc["a"] = a\nsha(calc)\n', encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    verificada = fingerprint(guard.snapshot())

    # ... and then something writes. In the measured run, one line went missing.
    alvo.write_text('calc.pop("a")\nsha(calc)\n', encoding="utf-8")

    resposta = _agente(guard)._delivered_matches_verified(_resultado(_venceu(verificada)))

    assert resposta is False, (
        "this is the whole finding: verified said True about a tree that is no longer on disk"
    )


def test_an_untouched_tree_matches(tmp_path: Path) -> None:
    (tmp_path / "kernel.py").write_text("x = 1\n", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    verificada = fingerprint(guard.snapshot())

    resposta = _agente(guard)._delivered_matches_verified(_resultado(_venceu(verificada)))

    assert resposta is True


def test_a_new_file_after_the_verdict_is_reported(tmp_path: Path) -> None:
    (tmp_path / "kernel.py").write_text("x = 1\n", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    verificada = fingerprint(guard.snapshot())

    (tmp_path / "extra.py").write_text("import os\n", encoding="utf-8")

    assert _agente(guard)._delivered_matches_verified(_resultado(_venceu(verificada))) is False


# --------------------------------------------------------------------------------------------
# 3. "We did not look" must not read as "it matched"


@pytest.mark.parametrize(
    "porque, resultado, com_guard",
    [
        ("nenhuma tentativa venceu", _resultado(Attempt(1, "", False, False, True, False)), True),
        ("nenhuma tentativa", _resultado(), True),
        ("sem digest (recibo antigo)", _resultado(_venceu("")), True),
    ],
)
def test_not_checkable_is_none_and_never_true(
    tmp_path: Path, porque: str, resultado: AutonomousResult, com_guard: bool
) -> None:
    guard = WorkspaceGuard(tmp_path) if com_guard else None
    assert _agente(guard)._delivered_matches_verified(resultado) is None, porque


def test_no_guard_means_not_checkable(tmp_path: Path) -> None:
    assert _agente(None)._delivered_matches_verified(_resultado(_venceu("abc"))) is None


def test_the_digest_read_is_the_WINNING_attempt_and_not_the_first(tmp_path: Path) -> None:
    """A retried run has a losing attempt in front of the one that decided it.

    Every other test here has exactly one attempt, where first, last and winner are the same object
    — so reading `attempts[0]` instead of the successful one passes all of them. A run that failed
    once and then succeeded is the only shape that tells them apart, and it is the ordinary shape:
    `max_attempts` defaults to 3.
    """
    (tmp_path / "kernel.py").write_text("x = 1\n", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    verificada = fingerprint(guard.snapshot())

    perdeu = Attempt(1, "nao deu", False, False, True, False)
    perdeu.verified_fingerprint = "digest-de-uma-arvore-que-foi-revertida"
    ganhou = _venceu(verificada)
    ganhou.index = 2

    resposta = _agente(guard)._delivered_matches_verified(_resultado(perdeu, ganhou))

    assert resposta is True, (
        "the reverted attempt's digest describes a tree that was deliberately thrown away; "
        "comparing against it reports every retried run as mismatched"
    )


def test_the_check_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run that finished must get a receipt; a status read cannot be what stops one."""
    guard = WorkspaceGuard(tmp_path)
    monkeypatch.setattr(
        WorkspaceGuard, "snapshot", lambda _s: (_ for _ in ()).throw(OSError("disk gone"))
    )

    assert _agente(guard)._delivered_matches_verified(_resultado(_venceu("abc"))) is None


# --------------------------------------------------------------------------------------------
# 4. It reaches the receipt, and the digest is kept so a reader can recompute it


def test_the_receipt_carries_the_answer() -> None:
    from chimera.api.runs import build_receipt

    r = build_receipt(
        _resultado(_venceu("abc")), "tarefa", "pytest", "2026-09-03T00:00:00Z",
        delivered_matches_verified=False,
    )
    assert r.delivered_matches_verified is False


def test_an_old_receipt_reads_as_unknown_not_as_matching() -> None:
    """Defaulting to True would stamp the stronger claim on every row already in the file."""
    from chimera.api.runs import build_receipt

    r = build_receipt(_resultado(_venceu("abc")), "t", None, "2026-09-03T00:00:00Z")
    assert r.delivered_matches_verified is None


def test_the_attempt_digest_survives_into_the_receipt() -> None:
    """Kept so the claim can be RECOMPUTED by a reader, which is the point of it existing."""
    from chimera.api.runs import build_receipt

    r = build_receipt(_resultado(_venceu("dedo-digital")), "t", None, "2026-09-03T00:00:00Z")
    assert r.attempts[0].verified_fingerprint == "dedo-digital"


# --------------------------------------------------------------------------------------------
# 5. The wiring, which is the part every test above takes on faith
#
# Everything before this point PLANTS a fingerprint on the attempt by hand. That proves the
# comparison, and proves nothing about whether a real run ever records one — the exact shape of the
# guard that was written, committed and never called. This runs the loop.


def _agente_real(ws: Path, log: Path, escreve: str = "# feito\n") -> AutonomousAgent:
    class _Escreve:
        def run(self, _task: str) -> object:
            from chimera.core.agent import AgentResult

            (ws / "done.py").write_text(escreve, encoding="utf-8")
            return AgentResult(answer="pronto", steps=1, stopped_reason="final")

    return AutonomousAgent(
        _Escreve(),
        guard=WorkspaceGuard(ws),
        workspace=ws,
        run_log=log,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )


def test_a_real_run_records_a_fingerprint_without_anybody_planting_one(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()

    resultado = _agente_real(ws, tmp_path / "runs.jsonl").run("escreva done.py")

    assert resultado.success, resultado.answer
    vencedora = next(a for a in resultado.attempts if a.success)
    assert vencedora.verified_fingerprint, (
        "the field exists, the comparison works, and nothing on the run path ever filled it in — "
        "which is a guard written and never called"
    )
    assert len(vencedora.verified_fingerprint) == 64, "a sha256 hexdigest"


def test_a_real_run_writes_the_answer_into_its_receipt(tmp_path: Path) -> None:
    import json

    ws = tmp_path / "ws"
    ws.mkdir()
    log = tmp_path / "runs.jsonl"

    _agente_real(ws, log).run("escreva done.py")

    linhas = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert linhas, "the run wrote no receipt at all"
    assert linhas[-1]["delivered_matches_verified"] is True, (
        "nothing touched the tree after the verdict, so the delivered tree IS the verified one"
    )


def test_a_real_run_reports_false_when_the_tree_moves_under_it(tmp_path: Path) -> None:
    """The measured failure, driven through the real loop rather than a hand-built result."""
    import json

    ws = tmp_path / "ws"
    ws.mkdir()
    log = tmp_path / "runs.jsonl"
    agente = _agente_real(ws, log)

    # Stand in for whatever wrote after the verdict in the measured run. Wrapping the receipt call
    # is the only place a test can be sure it lands AFTER the digest was taken and BEFORE the row
    # is written — which is precisely the window the finding is about.
    original = agente._persist_receipt

    def escreve_depois(result: object, task: str) -> None:
        (ws / "done.py").write_text("# alguem escreveu depois\n", encoding="utf-8")
        original(result, task)  # type: ignore[arg-type]

    agente._persist_receipt = escreve_depois  # type: ignore[method-assign]
    agente.run("escreva done.py")

    linhas = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert linhas[-1]["success"] is True, "the run still succeeded — that verdict was true when given"
    assert linhas[-1]["delivered_matches_verified"] is False, (
        "and the row now says the delivered tree is not the one that verdict was about"
    )
