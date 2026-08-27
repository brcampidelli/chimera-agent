"""The crew route's dollar ceiling, and its bill.

`CrewRunIn` inherits `max_usd` from `CodeSeams`. The schema documents it, `gen:api` publishes it to
the TypeScript client, and the route read it for nothing at all — while the hierarchy route beside
it, started from the same screen, wraps its gateway in `SpendCappedBackend` and says so in a comment
naming this exact risk.

The crew is the more expensive of the two: N workers that each run a full tool-using loop in their
own worktree, plus an optional top-model synthesis. The route that ignored its ceiling was the route
that needed one.

The same asymmetry ran through the accounting: `_record_run_spend` puts a hierarchy run on the Cost
screen, and a crew run appeared nowhere — so the screen that answers "what has this cost me" was
blind to the costlier half of the feature.

These tests assert the WIRING. A ceiling class that works and a route that never builds one is the
shape of defect this file exists for, and it is the one a test of `SpendCappedBackend` would pass
through without noticing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from chimera.api.orchestration_api import register_orchestration_api
from chimera.config import Settings
from tests.test_hierarchy import FakeBackend


def _sse(texto: str) -> list[tuple[str, dict[str, Any]]]:
    """The frames of an SSE response, as (event, payload)."""
    quadros: list[tuple[str, dict[str, Any]]] = []
    evento = ""
    for linha in texto.splitlines():
        if linha.startswith("event:"):
            evento = linha[6:].strip()
        elif linha.startswith("data:") and evento:
            quadros.append((evento, json.loads(linha[5:].strip())))
    return quadros


class CountingBackend(FakeBackend):
    """A backend that reports a price, so a ceiling has something to count against."""

    def __init__(self) -> None:
        super().__init__()
        self.wrapped_by: list[str] = []


@pytest.fixture
def cliente(tmp_path: Path) -> tuple[TestClient, CountingBackend, Path]:
    backend = CountingBackend()
    app = FastAPI()
    home = tmp_path / "home"
    settings = Settings(CHIMERA_HOME=str(home))
    register_orchestration_api(
        app, Depends(lambda: None), tmp_path, settings, backend_factory=lambda: backend
    )
    return TestClient(app), backend, home


def _crew(client: TestClient, **over: Any) -> list[tuple[str, dict[str, Any]]]:
    body = {
        "task": "corrija o bug do desconto",
        "workers": [{"name": "cauteloso", "instruction": "Faça a menor mudança possível."}],
        **over,
    }
    return _sse(client.post("/api/orchestration/crew", json=body).text)


def test_the_ceiling_reaches_the_gateway(
    cliente: tuple[TestClient, CountingBackend, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring, asserted where the wire is: does anything wrap the backend when max_usd is set?

    Patched at the import site the route uses, so this fails if the route stops calling it — which
    is precisely what it had never started doing.
    """
    client, backend, _ = cliente
    envolvido: list[float] = []

    import chimera.orchestration.budget as budget_mod

    real = budget_mod.SpendCappedBackend

    class Espiao(real):  # type: ignore[misc, valid-type]
        def __init__(self, inner: Any, budget: Any) -> None:
            envolvido.append(float(getattr(budget, "max_usd", 0) or 0))
            super().__init__(inner, budget)

    monkeypatch.setattr(budget_mod, "SpendCappedBackend", Espiao)

    _crew(client, max_usd=0.25)

    assert envolvido, "the crew ran without wrapping its gateway in a spend cap"
    assert envolvido[0] == pytest.approx(0.25)


def test_no_ceiling_means_no_wrapper(
    cliente: tuple[TestClient, CountingBackend, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control. Wrapping unconditionally would pass the test above and put a budget of zero —
    which `SpendBudget` reads as "spend nothing" — on every run that asked for no ceiling."""
    client, _, _ = cliente
    envolvido: list[float] = []

    import chimera.orchestration.budget as budget_mod

    real = budget_mod.SpendCappedBackend

    class Espiao(real):  # type: ignore[misc, valid-type]
        def __init__(self, inner: Any, budget: Any) -> None:
            envolvido.append(float(getattr(budget, "max_usd", 0) or 0))
            super().__init__(inner, budget)

    monkeypatch.setattr(budget_mod, "SpendCappedBackend", Espiao)

    _crew(client)  # no max_usd

    assert not envolvido, "a run that asked for no ceiling was given one anyway"


def test_a_crew_run_reaches_the_cost_screen(
    cliente: tuple[TestClient, CountingBackend, Path],
) -> None:
    """`_record_run_spend` was called on the hierarchy path and not here, so the Cost screen was
    blind to the more expensive of the two routes."""
    client, _, home = cliente
    gravados: list[dict[str, Any]] = []

    import chimera.api.orchestration_api as mod

    real = mod._record_run_spend

    def espiao(h: Any, run_id: str, outcome: Any) -> None:
        gravados.append({"run_id": run_id, "outcome": outcome})
        real(h, run_id, outcome)

    mod._record_run_spend = espiao  # type: ignore[assignment]
    try:
        _crew(client)
    finally:
        mod._record_run_spend = real  # type: ignore[assignment]

    assert gravados, "a crew run recorded no spend at all"
    assert gravados[0]["run_id"], "the run id is what joins the bill to the transcript"
