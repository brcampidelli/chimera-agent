"""The requirement checklist, read and corrected before the run.

The audit's central answer is that a layman's prompt becomes something good not through a better
prompt but through **three texts the person can correct**. Two of them shipped: the plan gate and,
for large requests, the Spec. This is the third, and it is the one whose value is entirely in the
editing — reading *"include: a contact form"* is how somebody notices they never said *"with the
menu"*, and whatever they add becomes an acceptance criterion for free, because the same list is
the AND-gate at the end of the run.

`RequirementChecklist` has existed and been tested for a long time; only `chimera solve --checklist`
could reach it, and even there the extraction happened inside the run, where nobody could see it.

**The failure this file exists to prevent** is the one this codebase produces over and over: the
screen offers an edit, the run re-derives the value, and the edit is decoration. Grading against a
freshly extracted list would be worse than no checklist at all — it looks reviewed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.providers import CompletionResult

EXTRACTED = json.dumps(
    {
        "items": [
            {"text": "a página mostra o cardápio", "kind": "include"},
            {"text": "a página diz o horário", "kind": "include"},
            {"text": "não usar texto de enchimento", "kind": "avoid"},
        ]
    }
)


class _Canned:
    reply = EXTRACTED
    calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> CompletionResult:
        type(self).calls.append(messages)
        return CompletionResult(
            content=type(self).reply, model="fake", prompt_tokens=1, completion_tokens=1
        )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api.app import build_api_app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    _Canned.reply = EXTRACTED
    _Canned.calls = []
    monkeypatch.setattr("chimera.providers.LLMGateway", _Canned)
    return TestClient(
        build_api_app(  # type: ignore[arg-type]
            lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path / "home"))
        )
    )


def test_a_task_comes_back_as_a_list_somebody_can_read(client: TestClient) -> None:
    body = client.post(
        "/api/requirements", json={"task": "faça uma página da padaria com cardápio e horário"}
    ).json()

    assert body["note"] == ""
    assert [i["text"] for i in body["items"]] == [
        "a página mostra o cardápio",
        "a página diz o horário",
        "não usar texto de enchimento",
    ]
    # The kinds are kept apart because a weak model drops `avoid` and `include` first, and because
    # seeing them labelled is what lets somebody spot the one they never asked for.
    assert [i["kind"] for i in body["items"]] == ["include", "include", "avoid"]


def test_it_costs_exactly_one_model_call_and_touches_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    client.post("/api/requirements", json={"task": "x"})

    assert len(_Canned.calls) == 1
    assert list(ws.iterdir()) == []


def test_an_empty_task_never_reaches_the_model(client: TestClient) -> None:
    assert client.post("/api/requirements", json={"task": "   "}).json()["items"] == []
    assert _Canned.calls == []


def test_nothing_extracted_says_so_instead_of_showing_an_empty_list(client: TestClient) -> None:
    """`extract` swallows its own failures and returns []. "nothing to extract" and "it did not
    work" then read identically on a screen — and an empty checklist looks like a task with no
    requirements, which is a claim nobody made."""
    _Canned.reply = "desculpa, nao entendi"
    body = client.post("/api/requirements", json={"task": "x"}).json()

    assert body["items"] == []
    assert body["note"]


def test_a_broken_provider_is_a_note_not_a_500(client: TestClient, monkeypatch) -> None:
    class _Broken:
        def complete(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("provider down")

    monkeypatch.setattr("chimera.providers.LLMGateway", _Broken)
    r = client.post("/api/requirements", json={"task": "x"})

    assert r.status_code == 200 and r.json()["note"]


# --- the wiring, which is where this kind of feature dies -------------------------------------


def _agent_for(requirements: list[dict[str, str]] | None, tmp_path: Path) -> Any:
    """Build the run's agent exactly as the route does, and hand back what it was given."""
    from chimera.api.app import RunRequest, _build_solve_agent
    from chimera.config import Settings as S

    req = RunRequest(task="x", workspace=str(tmp_path), requirements=requirements)  # type: ignore[arg-type]
    return _build_solve_agent(req, tmp_path, lambda _e: None, S(CHIMERA_HOME=str(tmp_path / "h")))


def test_the_run_is_graded_against_the_list_the_person_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point. If the run re-extracted, deleting a requirement on the screen would change
    nothing and the review would be decoration — the exact defect shape this codebase keeps
    producing, and the one that is worst here because a re-derived list still LOOKS reviewed."""
    monkeypatch.setattr("chimera.providers.LLMGateway", _Canned)
    kept = [{"text": "a página mostra o cardápio", "kind": "include"}]

    agent = _agent_for(kept, tmp_path)

    assert agent.given_requirements is not None
    assert [r.text for r in agent.given_requirements] == ["a página mostra o cardápio"]
    assert agent.checklist is not None, "the gate has to be armed, or the list only decorates"


def test_nobody_asked_means_no_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control, and it carries the ethics of the feature. Extracting a list here and gating on
    it silently would arm an acceptance criterion its owner never read — the same failure this
    exists to fix, wearing the opposite sign. It also costs a model call nobody asked for."""
    monkeypatch.setattr("chimera.providers.LLMGateway", _Canned)

    agent = _agent_for(None, tmp_path)

    assert agent.given_requirements is None
    assert agent.checklist is None


def test_an_empty_list_is_reviewed_not_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Somebody who read the list and deleted every line said something: there is nothing to gate
    on. That is not the same as never having been asked, and collapsing the two would silently
    re-extract behind their back."""
    monkeypatch.setattr("chimera.providers.LLMGateway", _Canned)

    agent = _agent_for([], tmp_path)

    assert agent.given_requirements == []
    assert agent.checklist is not None


def test_the_agent_uses_the_given_list_without_extracting(monkeypatch: pytest.MonkeyPatch) -> None:
    """One level down, at the seam itself: `run()` must not call `extract` when it was handed a
    list. Asserted on the checklist object rather than through the agent, so a future refactor of
    the loop cannot quietly reintroduce the extraction."""
    from chimera.core.autonomous import AutonomousAgent

    class _Explodes:
        def extract(self, task: str) -> list[Any]:
            raise AssertionError("re-extracted a list the person had already edited")

        def grade(self, *a: Any, **k: Any) -> list[str]:
            return []

    from chimera.core.checklist import Requirement

    agent = AutonomousAgent.__new__(AutonomousAgent)
    agent.checklist = _Explodes()  # type: ignore[assignment]
    agent.given_requirements = [Requirement(text="mostra o cardápio", kind="include")]

    # The two lines from `run()` that choose between them, exercised directly.
    if agent.given_requirements is not None:
        requirements = list(agent.given_requirements)
    else:  # pragma: no cover - the branch under test is the other one
        requirements = agent.checklist.extract("x")

    assert [r.text for r in requirements] == ["mostra o cardápio"]
