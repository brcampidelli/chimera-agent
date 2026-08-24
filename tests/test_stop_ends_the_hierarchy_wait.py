"""Stop must end the hierarchy's wait, and must not be reported as a deadline.

`run_all_with_deadline` takes a `cancelled` callable, and it is the ONLY thing that lets a batch
give up on a worker that cannot hear a stop flag. Cooperative flags are read *between* units —
before one starts, after its model call returns — so a worker parked inside a model call never
reads one. `run_isolated` was given the argument when a live consumer was first attached to the
crew (`isolation.py`); `_dispatch` is the same shape and the same call and did not get it.

What that cost, measured against the running app: pressing Stop returned `{"ok": true,
"cancelled": true}` and the run kept waiting — up to `CHIMERA_BATCH_TIMEOUT`, four hours, in a
desktop app with somebody watching. The SSE stream stayed open behind it.

The deadline is shortened here so the control can run at all: without a bound, the un-cancelled
half of this file would wait four hours to make its point.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from chimera.orchestration.artifacts import ArtifactStore
from chimera.orchestration.envelope_verify import EnvelopeVerifier
from chimera.orchestration.hierarchy import HierarchicalOrchestrator
from chimera.providers.gateway import CompletionResult

DEADLINE_S = 3.0
#: Comfortably under `DEADLINE_S`, so "it came back fast" cannot be the deadline firing early.
DEPRESSA_S = 1.5


class _Backend:
    def complete(self, messages: Any, *, model: str | None = None, **_kw: Any) -> CompletionResult:
        texto = " ".join(
            (m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or ""
            for m in messages
        )
        conteudo = (
            '[{"task_id": "sub-1", "objective": "Read the file", "output_format": "prose"}]'
            if "subtask" in texto.lower() or "objective" in texto.lower()
            else "Synthesised answer."
        )
        return CompletionResult(content=conteudo, model=model or "?", prompt_tokens=10, completion_tokens=5)


def _corrida(tmp_path: Path, monkey: pytest.MonkeyPatch, *, parar: bool):
    """One fan-out whose single worker blocks forever inside `act` — a model call that hangs.

    Returns `(result, events, elapsed)`. With `parar`, the stop flag goes up the moment the worker
    starts, which is the real sequence: nobody presses Stop before the run has begun.
    """
    from chimera import concurrency
    from chimera.orchestration import hierarchy as hier_mod
    from chimera.orchestration import isolation

    monkey.setattr(concurrency, "_CANCEL_GRACE_S", 0.05)
    monkey.setattr(isolation, "_batch_deadline", lambda _v: DEADLINE_S)

    arrancou = threading.Event()
    preso = threading.Event()  # never set; the worker waits on it the way a hung call would

    class _Worker:
        name = "w"
        last_stop = "final"

        def act(self, _task: str, **_kw: Any) -> str:
            arrancou.set()
            preso.wait(30)  # bounded only so a broken test cannot leak a thread for the session
            return "never reported"

    monkey.setattr(hier_mod, "RoleAgent", lambda *_a, **_kw: _Worker())

    eventos: list[dict[str, Any]] = []
    store = ArtifactStore(tmp_path / "artifacts")
    orq = HierarchicalOrchestrator(
        _Backend(),
        weak_model="w",
        mid_model="m",
        top_model="t",
        store=store,
        verifier=EnvelopeVerifier(store=store, backend=None, spot_rate=0.0),
        receipts_path=tmp_path / "delegations.jsonl",
        worker_tools=lambda: object(),
        should_stop=(lambda: arrancou.is_set()) if parar else (lambda: False),
        on_event=lambda e: eventos.append({"kind": e.kind, **(e.data or {})}),
    )

    comecou = time.monotonic()
    resultado = orq.run("Read doc A and doc B and compare them.")
    return resultado, eventos, time.monotonic() - comecou


def _rejeicoes(eventos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in eventos if e["kind"] == "worker_rejected"]


def test_stop_does_not_wait_for_the_batch_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resultado, _eventos, decorrido = _corrida(tmp_path, monkeypatch, parar=True)

    assert decorrido < DEPRESSA_S, (
        f"the wait ran for {decorrido:.1f}s with a stop flag up — in production that bound is "
        "CHIMERA_BATCH_TIMEOUT, four hours, and Stop had already answered ok"
    )
    assert resultado.cancelled is True


def test_a_stopped_worker_is_not_reported_as_a_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both abandon the worker. Only one of them is something the person watching did."""
    _resultado, eventos, _decorrido = _corrida(tmp_path, monkeypatch, parar=True)

    rejeitados = _rejeicoes(eventos)
    assert rejeitados, "the worker was abandoned and the screen was told nothing"
    assert rejeitados[0]["reason"] == "cancelled", (
        f"reported {rejeitados[0]['reason']!r} — telling somebody who just pressed Stop that their "
        "subtask overran a deadline describes the mechanism and misnames the cause"
    )


def test_a_real_overrun_is_still_a_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control, and the reason the two cannot be one branch.

    Same worker, same hang, same abandonment — only the flag differs. Without this, reporting
    *every* abandoned worker as cancelled would pass the test above and lose the distinction the
    fix exists to create.
    """
    _resultado, eventos, decorrido = _corrida(tmp_path, monkeypatch, parar=False)

    assert decorrido >= DEADLINE_S - 0.5, "it gave up before the deadline with nothing telling it to"
    rejeitados = _rejeicoes(eventos)
    assert rejeitados and rejeitados[0]["reason"] == "deadline"
