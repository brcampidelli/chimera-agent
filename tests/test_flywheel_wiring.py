"""The desktop app learns from its own work.

The app was the surface that did the most work and learned the least from it. `chimera solve` in a
terminal accumulated long-term memory, minted skills and grew a playbook; every run started from the
app threw all of it away, because ``/api/runs`` built its agent without a single learning seam. The
gap was invisible from the outside: two runs of the same task, one from a terminal and one from the
app, produced the same receipt and left the system in different states.

These tests hold the wiring, and — more importantly — the two things that must stay true once a run
can WRITE to memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")


def _agent(tmp_path: Path, **over: Any) -> Any:
    from chimera.api.app import RunRequest, _build_solve_agent
    from chimera.config import Settings

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return _build_solve_agent(RunRequest(task="t", **over), ws, lambda _e: None, settings)


def test_a_desktop_run_carries_the_learning_seams(tmp_path: Path) -> None:
    # Asserted through the real builder rather than the factory, because the factory was never the
    # problem — this call site simply never called it.
    agent = _agent(tmp_path)

    assert agent.memory is not None
    assert agent.experience is not None
    assert agent.playbook is not None
    # A cunhagem segue a leitura desde que as duas metades passaram a se olhar. O que este arquivo
    # existe para segurar e' a PARIDADE — o terminal e o app tem de terminar no mesmo estado — e
    # ela continua de pe: os dois deixam de cunhar quando o agente nao pode reler.
    assert agent.auto_evolver is None


def test_ligar_a_leitura_devolve_a_cunhagem_ao_app(tmp_path: Path, monkeypatch: Any) -> None:
    """E a paridade vale nos dois sentidos: quem liga a leitura volta a cunhar PELO APP tambem,
    sem precisar descobrir um segundo interruptor."""
    monkeypatch.setenv("CHIMERA_SKILL_CARDS", "1")

    assert _agent(tmp_path).auto_evolver is not None


def test_trajectory_collection_stays_off(tmp_path: Path) -> None:
    # Not an oversight and not part of the flywheel: the collector writes every step of every run to
    # a JSONL dataset for export and training. That is a deliberate act with a disk cost, gated
    # behind `--collect` on the CLI, and turning it on for everyone because it sits in the same
    # factory would be shipping data collection under the name of learning.
    assert _agent(tmp_path).trajectories is None


def test_the_taint_ledger_reaches_the_agent_even_without_a_thread(tmp_path: Path) -> None:
    """The precondition of everything above, and the reason it had to be fixed first.

    The ledger used to be passed only when the client supplied a thread id, gated alongside the
    checkpointer. That gating is right for the checkpointer — a pause with no durable identity is a
    run nobody can come back to — and wrong for the ledger, which only observes.

    With no ledger, ``run_tainted()`` is not "unknown", it is ``False``. Before this change that
    false answer was harmless. Now the run writes to long-term memory, and it would store a fact
    learned from untrusted content with ``provenance="clean"`` — the precise poisoning path the
    provenance field exists to prevent, and one that survives into every future conversation.
    """
    assert _agent(tmp_path).taint is not None
    assert _agent(tmp_path, thread_id="t1").taint is not None


def test_pausing_is_still_gated_on_a_thread(tmp_path: Path) -> None:
    # Passing the ledger always must not turn every run into a pausing run: a pause needs somewhere
    # durable to wait, and that is what the thread id provides.
    assert _agent(tmp_path).checkpointer is None
    assert _agent(tmp_path, thread_id="t1").checkpointer is not None


def test_a_tainted_run_writes_a_fact_marked_as_tainted(tmp_path: Path) -> None:
    # Approval sanctions the action, not the content's trust: a fact learned while reading untrusted
    # content stays labelled, and the label is what makes the recall say "[unverified]" later.
    from chimera.core.autonomous import AutonomousAgent

    written: list[dict[str, Any]] = []

    class _Memory:
        def remember(
            self, fact: str, *, key: str, provenance: str = "clean",
            project: str | None = None,
        ) -> None:
            written.append({"fact": fact, "key": key, "provenance": provenance})

    agent = object.__new__(AutonomousAgent)
    agent.memory = _Memory()
    # The method also reads the workspace now, to scope the fact to the folder the work happened
    # in. A bare object has to carry every attribute the method under test touches; None is the
    # honest value here, and it means the fact belongs everywhere.
    agent.workspace = None
    AutonomousAgent._remember_success(agent, "fix the parser", "done", tainted=True)

    assert written[0]["provenance"] == "tainted"
    assert written[0]["key"].startswith("solve:")
    # One short keyed fact — never the transcript, never file content. Re-solving the same task
    # UPDATEs this entry instead of growing the store.
    assert written[0]["fact"].startswith("Accomplished: fix the parser")
