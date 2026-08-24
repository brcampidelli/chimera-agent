"""End to end: a worker cut off mid-run is REJECTED, not verified.

The unit half of this is `test_worker_cut_off_is_not_a_finding.py` — that the stop reason survives
`RoleAgent.act`. This is the half that matters to a person looking at the screen: the card must be
red and say why, and the text must not reach the synthesiser as a finding.

Measured before the fix, against the running app with a 400-token cap: `verified (accepted)`, a
44-character summary that was the budget error itself, zero evidence, and a `done` frame reporting
`fell_back=false`. Every signal above the answer said success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.orchestration.artifacts import ArtifactStore
from chimera.orchestration.envelope_verify import EnvelopeVerifier
from chimera.orchestration.hierarchy import HierarchicalOrchestrator
from chimera.providers.gateway import CompletionResult

WEAK, MID, TOP = "w", "m", "t"


class _Backend:
    """Answers the decompose call with one subtask and everything else with prose."""

    def complete(self, messages: Any, *, model: str | None = None, **_kw: Any) -> CompletionResult:
        texto = " ".join(
            (m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or ""
            for m in messages
        )
        if "subtask" in texto.lower() or "objective" in texto.lower():
            conteudo = '[{"task_id": "sub-1", "objective": "Read the file", "output_format": "prose"}]'
        else:
            conteudo = "Synthesised answer."
        return CompletionResult(content=conteudo, model=model or "?", prompt_tokens=10, completion_tokens=5)


def _corrida(tmp_path: Path, motivo: str, resposta: str, eventos: list[dict[str, Any]], monkey: pytest.MonkeyPatch):
    """Run one fan-out where the single worker stops for `motivo` and returns `resposta`."""
    store = ArtifactStore(tmp_path / "artifacts")

    class _Worker:
        name = "w"

        def __init__(self) -> None:
            self.last_stop = motivo

        def act(self, _task: str, **_kw: Any) -> str:
            return resposta

    orq = HierarchicalOrchestrator(
        _Backend(),
        weak_model=WEAK,
        mid_model=MID,
        top_model=TOP,
        store=store,
        verifier=EnvelopeVerifier(store=store, backend=None, spot_rate=0.0),
        receipts_path=tmp_path / "delegations.jsonl",
        worker_tools=lambda: object(),
        on_event=lambda e: eventos.append({"kind": e.kind, "task_id": e.task_id, **(e.data or {})}),
    )
    # Swap the class the dispatch constructs, rather than standing in a whole agent loop. The
    # thing under examination is one field — what `act()` leaves in `last_stop` — and everything
    # else on the path stays real.
    import chimera.orchestration.hierarchy as hier_mod

    monkey.setattr(hier_mod, "RoleAgent", lambda *_a, **_kw: _Worker())
    return orq.run("Read doc A and doc B and compare them.")


def _de(eventos: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in eventos if e["kind"] == kind]


def test_a_budget_cut_worker_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    eventos: list[dict[str, Any]] = []
    _corrida(tmp_path, "budget", "delegation budget exhausted: 1336/400 tokens", eventos, monkeypatch)

    assert not _de(eventos, "worker_verified"), "a cut-off worker came back verified again"
    rejeitados = _de(eventos, "worker_rejected")
    assert rejeitados, "it was neither verified nor rejected — it vanished"
    assert rejeitados[0]["reason"] == "budget", (
        f"the reason was folded into {rejeitados[0]['reason']!r}; a budget cut is the one case a "
        "user can act on and must not read as a provider fault"
    )


def test_the_cut_off_text_does_not_reach_the_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of rejecting it. An error string folded into a synthesis reads as a finding."""
    eventos: list[dict[str, Any]] = []
    resultado = _corrida(tmp_path, "budget", "delegation budget exhausted: 1336/400 tokens", eventos, monkeypatch)

    assert "budget exhausted" not in (resultado.answer or "")


def test_a_worker_that_finished_is_still_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarding the guard.

    Rejecting every worker would pass both tests above while breaking the feature. This is the
    control: the same path, the same shapes, only the stop reason differs.
    """
    eventos: list[dict[str, Any]] = []
    _corrida(tmp_path, "final", "index.html is the storefront page.", eventos, monkeypatch)

    assert _de(eventos, "worker_verified"), "a worker that answered normally was rejected"
    assert not _de(eventos, "worker_rejected")


def test_the_verdict_names_the_gates_that_ran(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """"accepted" only ever meant "no gate rejected", and for ordinary output that is ONE gate."""
    eventos: list[dict[str, Any]] = []
    _corrida(tmp_path, "final", "index.html is the storefront page.", eventos, monkeypatch)

    verificado = _de(eventos, "worker_verified")[0]
    assert verificado["checks_run"] == ["schema"], (
        f"reported {verificado['checks_run']} — criteria needs regex lines in a prose output_format "
        "and the spot check needs evidence refs that only exist above the 8000-char cap, so a "
        "verdict claiming more than schema here would be claiming a check that cannot have run"
    )
