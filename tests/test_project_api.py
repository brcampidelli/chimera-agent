"""Creating a project, and making it move.

The app could approve a project and deny one, and could do neither of the two things that come
first: create it, or advance it. The HITL gate was on the screen; the loop it gates was not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings

SPEC = """
name: demo
requirements:
  - id: r1
    text: the check passes
    check: command
    target: "true"
    required: true
"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Same environment-rather-than-injection reasoning as the Kanban suite: `register_features`
    reads `get_settings()` directly, so the injected object configures the app and not these
    routes."""
    from chimera.api.app import build_api_app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    return TestClient(
        build_api_app(  # type: ignore[arg-type]
            lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path / "home"))
        )
    )


@pytest.fixture
def spec(tmp_path: Path) -> Path:
    """A spec that is already satisfied — its check exits 0, so the project has nothing to do."""
    path = tmp_path / "spec.yaml"
    path.write_text(SPEC, encoding="utf-8")
    return path


@pytest.fixture
def unaligned(tmp_path: Path) -> Path:
    """A spec with work to do: its check exits 1, so the project has a requirement to satisfy."""
    path = tmp_path / "unaligned.yaml"
    path.write_text(SPEC.replace('target: "true"', 'target: "false"'), encoding="utf-8")
    return path


def test_a_project_can_be_created_from_the_app(client: TestClient, spec: Path, tmp_path: Path) -> None:
    created = client.post(
        "/api/projects", json={"spec": str(spec), "workspace": str(tmp_path)}
    ).json()

    # `planning`, not `awaiting_approval`: the pause is something a STEP reaches, and this endpoint
    # deliberately does not step. Asserting the status I assumed rather than the one it has would
    # have made this test a description of my expectations.
    assert created["status"] == "planning"
    assert created["plan_approved"] is False
    assert [p["id"] for p in client.get("/api/projects").json()] == [created["id"]]


def test_creating_a_project_spends_nothing(client: TestClient, spec: Path, tmp_path: Path) -> None:
    """It pauses for plan approval and stops there.

    The CLI's `project start` creates AND runs, which suits a terminal. Here they are separate calls
    so the screen can show what was created — its id, its pause — before anything costs money. That
    this test passes without a provider key configured is the assertion.
    """
    created = client.post(
        "/api/projects", json={"spec": str(spec), "workspace": str(tmp_path)}
    ).json()
    assert created["iterations"] == 0


def test_a_project_with_work_to_do_stops_for_plan_approval(
    client: TestClient, unaligned: Path, tmp_path: Path
) -> None:
    """A project that starts working before anyone read its plan is a project nobody chose to run.

    Asserted through what a STEP does, not through a flag: `plan_approved` is false at creation
    either way, because `auto_approve` decides whether the pause is ever REACHED rather than
    recording an approval. Checking the flag would have passed while proving nothing.

    Needs a spec with work to do. The gate sits after the drift check, so an already-satisfied
    project reaches `done` without ever being asked — which is right, and which is exactly how the
    first version of this test "passed" against the wrong thing.

    The other half — auto_approve stepping THROUGH the gate — is deliberately not asserted here: past
    the gate the step works a card, and that calls a model.
    """
    project = client.post(
        "/api/projects", json={"spec": str(unaligned), "workspace": str(tmp_path)}
    ).json()
    paused = client.post(f"/api/projects/{project['id']}/step").json()

    assert paused["status"] == "awaiting_approval"
    assert "approve" in paused["note"]


def test_a_spec_that_is_not_there_is_refused_with_the_path(client: TestClient) -> None:
    bad = client.post("/api/projects", json={"spec": "/nowhere/spec.yaml"})
    assert bad.status_code == 400 and "/nowhere/spec.yaml" in bad.json()["detail"]


def test_a_spec_that_verifies_nothing_is_refused_with_a_reason(
    client: TestClient, tmp_path: Path
) -> None:
    """The orchestrator refuses it because an all-optional spec reports "done" having checked
    nothing. Surfacing the reason rather than a 500 is what makes that refusal usable."""
    empty = tmp_path / "empty.yaml"
    empty.write_text("name: demo\nrequirements: []\n", encoding="utf-8")

    bad = client.post("/api/projects", json={"spec": str(empty), "workspace": str(tmp_path)})
    assert bad.status_code == 400
    assert "no required requirements" in bad.json()["detail"]


def test_stepping_a_project_that_does_not_exist_is_a_404(client: TestClient) -> None:
    assert client.post("/api/projects/nope/step").status_code == 404


def test_one_step_is_one_iteration(client: TestClient, spec: Path, tmp_path: Path) -> None:
    """No server-side `run` loop, and that is a decision rather than an omission: `run()` is
    repeated `step()`, and a client-side loop can be stopped between iterations and shows the state
    after each one. A server-side run would be neither interruptible nor observable until it ended.

    Auto-approved so the step is not simply the approval pause returning unchanged.
    """
    created = client.post(
        "/api/projects",
        json={"spec": str(spec), "workspace": str(tmp_path), "auto_approve": True},
    ).json()

    # The spec's only requirement is satisfied by a command that exits 0, so the first step aligns
    # and finishes without a card ever reaching a model.
    stepped = client.post(f"/api/projects/{created['id']}/step").json()
    assert stepped["id"] == created["id"]
    assert stepped["status"] == "done"


def test_a_finished_project_stays_finished(client: TestClient, spec: Path, tmp_path: Path) -> None:
    """`done` is terminal. Stepping it again must not restart work someone already accepted."""
    created = client.post(
        "/api/projects",
        json={"spec": str(spec), "workspace": str(tmp_path), "auto_approve": True},
    ).json()
    first: dict[str, Any] = client.post(f"/api/projects/{created['id']}/step").json()
    again: dict[str, Any] = client.post(f"/api/projects/{created['id']}/step").json()

    assert first["status"] == again["status"] == "done"
    assert again["iterations"] == first["iterations"]
