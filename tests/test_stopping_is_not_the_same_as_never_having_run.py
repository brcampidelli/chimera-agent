"""Pressing Stop threw away the attempt, its cost, and any record of what it had written.

The two finalizers are siblings and only one of them was corrected. `_finalize_capped` carries a
comment saying exactly what it cost to learn: *"returning here before the normal bookkeeping left a
receipt reading `usd: null, attempts: []` for a run that had just spent money... the Cost screen
showed a paid run as free. A cap that hides its own spending is worse than no cap."*

`_finalize_cancelled` does that. The attempt is appended further down the loop, past the `return`,
so a run stopped after the worker came back persists `attempts: []` — and `_CANCELLED_ANSWER`, which
`agent.py` writes precisely so a stop does not read as "it produced nothing", is discarded with it.

The second half is the workspace. `guard.restore` only runs on the normal path, so a stopped run
leaves its files in place — correctly, because whoever pressed Stop may want the work — and the
partial attempt says `reverted: false` with `diffs: []`, which reads as "it changed nothing". The
right fix is to RECORD, not to revert: reverting would destroy work somebody may have wanted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.checkpoint import WorkspaceGuard


class _WorkerQueEscreveEPara:
    """A worker that writes a file and reports it was cancelled — the shape site 842 handles."""

    def __init__(self, workspace: Path, *, usd: float = 0.0031) -> None:
        self.workspace = workspace
        self.usd = usd
        self.chamadas = 0

    def run(self, *_args: Any, **_kwargs: Any) -> Any:
        self.chamadas += 1
        (self.workspace / "novo.py").write_text("print('meio do caminho')\n", encoding="utf-8")

        class _R:
            answer = "Stopped at your request. The work up to this point is in the transcript."
            stopped_reason = "cancelled"
            success = False
            steps = 3
            model = "prov/m"
            prompt_tokens = 900
            completion_tokens = 120
            run_id = "r-42"

        _R.usd = self.usd  # type: ignore[attr-defined]
        return _R()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "ja_existia.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _agente(workspace: Path, worker: Any) -> AutonomousAgent:
    return AutonomousAgent(
        worker,
        config=AutonomousConfig(max_attempts=1),
        guard=WorkspaceGuard(workspace),
    )


# ------------------------------------------------------------------ the attempt


def test_a_stopped_run_still_has_the_attempt_it_paid_for(workspace: Path) -> None:
    """The whole point, and the same assertion its sibling already carries."""
    resultado = _agente(workspace, _WorkerQueEscreveEPara(workspace)).run("faça algo")

    assert resultado.stopped_reason == "cancelled"
    assert len(resultado.attempts) == 1


def test_the_money_is_not_erased(workspace: Path) -> None:
    """A run that called a model and reports `usd: null` shows a paid run as free — the exact
    sentence written into `_finalize_capped` after it was measured on a real run."""
    resultado = _agente(workspace, _WorkerQueEscreveEPara(workspace, usd=0.0031)).run("faça algo")

    assert resultado.attempts[0].usd == pytest.approx(0.0031)
    assert resultado.attempts[0].prompt_tokens == 900


def test_the_answer_survives(workspace: Path) -> None:
    """`agent.py` writes `_CANCELLED_ANSWER` precisely so a stop does not read as "it produced
    nothing" — and the finalizer discarded it, restoring the meaning it exists to prevent."""
    resultado = _agente(workspace, _WorkerQueEscreveEPara(workspace)).run("faça algo")

    assert "Stopped at your request" in resultado.answer


def test_the_run_id_joins_it_to_the_trace(workspace: Path) -> None:
    """`AttemptReceipt.run_id` -> `traces.jsonl` is the one join this project documents, and an
    attempt with no id is an attempt nothing can be looked up about."""
    resultado = _agente(workspace, _WorkerQueEscreveEPara(workspace)).run("faça algo")

    assert resultado.attempts[0].run_id == "r-42"


# ------------------------------------------------------------------ the workspace


def test_a_stopped_run_says_the_files_changed(workspace: Path) -> None:
    """`reverted: false` with `diffs: []` reads as "it changed nothing", and the file is right there.

    This is the field's own promise: `diff_productive` exists to answer whether an attempt did any
    real work, and `null` for an attempt that wrote a file is the lie the field was added to stop.
    """
    resultado = _agente(workspace, _WorkerQueEscreveEPara(workspace)).run("faça algo")

    assert resultado.attempts[0].diff_summary
    assert resultado.attempts[0].diff_productive is True


def test_the_files_are_left_alone(workspace: Path) -> None:
    """RECORDED, not reverted. Whoever pressed Stop may want what the run wrote, and destroying it
    to make a receipt tidy is the wrong trade."""
    _agente(workspace, _WorkerQueEscreveEPara(workspace)).run("faça algo")

    assert (workspace / "novo.py").exists()
    assert resultado_intacto(workspace)


def resultado_intacto(workspace: Path) -> bool:
    return (workspace / "ja_existia.py").read_text(encoding="utf-8") == "x = 1\n"


def test_it_is_not_reported_as_reverted(workspace: Path) -> None:
    """The flag has to keep meaning what it says: nothing was rolled back here."""
    resultado = _agente(workspace, _WorkerQueEscreveEPara(workspace)).run("faça algo")

    assert resultado.attempts[0].reverted is False


# ------------------------------------------------------------------ the case with nothing to keep


def test_a_stop_before_the_first_call_records_no_attempt(workspace: Path) -> None:
    """The guard against inventing one. Two of the three cancel sites fire before any model call, and
    an attempt that never happened must not appear in a receipt — a run stopped before it started
    cost nothing and did nothing, and saying otherwise is the same fabrication in reverse.
    """

    class _NuncaChamado:
        def run(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - never reached
            raise AssertionError("o worker não deveria ser chamado")

    agente = AutonomousAgent(
        _NuncaChamado(),
        config=AutonomousConfig(max_attempts=1),
        guard=WorkspaceGuard(workspace),
        should_stop=lambda: True,
    )

    resultado = agente.run("faça algo")

    assert resultado.stopped_reason == "cancelled"
    assert resultado.attempts == []


def test_a_successful_run_is_untouched(workspace: Path) -> None:
    """The ordinary path keeps its own bookkeeping — this change adds a branch, it does not move
    the one that already worked."""

    class _Termina:
        def run(self, *_args: Any, **_kwargs: Any) -> Any:
            (workspace / "feito.py").write_text("ok\n", encoding="utf-8")

            class _R:
                answer = "pronto"
                stopped_reason = "final"
                success = True
                steps = 2
                model = "prov/m"
                prompt_tokens = 10
                completion_tokens = 5
                usd = 0.0
                run_id = "r-1"

            return _R()

    resultado = _agente(workspace, _Termina()).run("faça algo")

    assert resultado.stopped_reason != "cancelled"
    assert len(resultado.attempts) == 1
