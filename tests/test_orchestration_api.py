"""The orchestration HTTP surface. No provider key, no network — the gateway is injected.

What these protect, in order of how much it would cost to get wrong:

- the preview must not run anything (a "see the plan" button that spends is not a preview);
- the frame order and the sequence numbers, which are the contract a reloading client replays on;
- cancel answering 200 for a run that already ended, because that is the state a stale click hits.
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
from tests.test_hierarchy import _READ_TASK, FakeBackend


def _read_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[len("data:") :].strip())))
    return events


@pytest.fixture()
def app_and_backend(tmp_path: Path) -> tuple[TestClient, FakeBackend]:
    backend = FakeBackend()
    app = FastAPI()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    register_orchestration_api(
        app,
        Depends(lambda: None),
        tmp_path,
        settings,
        backend_factory=lambda: backend,
    )
    return TestClient(app), backend


def test_the_preview_shows_the_plan_and_starts_no_workers(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, backend = app_and_backend

    body = client.post("/api/orchestration/preview", json={"task": _READ_TASK}).json()

    assert body["shape"] == "parallel_read"
    assert len(body["subtasks"]) == 2
    assert body["would_fall_back"] is False
    # The one call it is allowed to make is the decompose, and it says so rather than claiming
    # to be free. No worker ever ran.
    assert body["decompose_spent"] is True
    assert not any(call["system"].startswith("You are a focused sub-worker") for call in backend.calls)


def test_a_write_task_previews_as_a_fallback_without_spending_anything(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, backend = app_and_backend

    body = client.post(
        "/api/orchestration/preview", json={"task": "Implement the retry and fix the test"}
    ).json()

    assert body["would_fall_back"] is True
    assert body["fell_back_reason"] == "shape"
    # This branch is fully deterministic — classification and the estimate are both arithmetic.
    assert body["decompose_spent"] is False
    assert backend.calls == []


def test_an_empty_task_is_refused_before_anything_is_built(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend
    assert client.post("/api/orchestration/preview", json={"task": "   "}).status_code == 400
    assert client.post("/api/orchestration/hierarchy", json={"task": ""}).status_code == 400


def test_the_stream_opens_with_the_run_id_and_closes_with_the_answer(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    response = client.post("/api/orchestration/hierarchy", json={"task": _READ_TASK})
    frames = _read_sse(response.text)
    kinds = [kind for kind, _ in frames]

    # The id arrives before any work, so a Stop control can target the run from the first moment.
    assert kinds[0] == "run"
    assert frames[0][1]["run_id"]
    assert kinds[-1] == "done"
    assert frames[-1][1]["answer"] == "Final synthesized answer."
    assert kinds.count("worker_started") == 2


def test_every_frame_is_numbered_and_the_numbers_only_go_up(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    frames = _read_sse(client.post("/api/orchestration/hierarchy", json={"task": _READ_TASK}).text)

    seqs = [payload["seq"] for _, payload in frames]
    # Strictly increasing with no gaps. A client that reconnects asks for everything after the
    # last number it saw; a repeated or missing one duplicates or loses a card.
    assert seqs == list(range(1, len(seqs) + 1))


def test_worker_frames_carry_a_task_id_the_decomposition_already_named(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    frames = _read_sse(client.post("/api/orchestration/hierarchy", json={"task": _READ_TASK}).text)
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for kind, payload in frames:
        by_kind.setdefault(kind, []).append(payload)

    published = {spec["task_id"] for spec in by_kind["decomposed"][0]["specs"]}
    started = {payload["task_id"] for payload in by_kind["worker_started"]}
    assert published == started and len(published) == 2


def test_a_write_task_streams_the_fallback_as_a_fact_not_an_error(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    frames = _read_sse(
        client.post(
            "/api/orchestration/hierarchy", json={"task": "Refactor the parser and fix the test"}
        ).text
    )
    kinds = [kind for kind, _ in frames]

    # No error frame: falling back is the single-agent path working as designed, and the most
    # common outcome by far. A client that renders `error` here tells the user it broke.
    assert "error" not in kinds
    assert kinds == ["run", "classified", "fell_back", "done"]
    fell = next(payload for kind, payload in frames if kind == "fell_back")
    assert fell["reason"] == "shape"


def test_cancelling_a_run_that_already_ended_is_not_an_error(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    response = client.post("/api/orchestration/runs/does-not-exist/cancel")

    # 200, not 404: a Stop click that lands after the run finished is a normal thing to happen,
    # not a client mistake, and a UI should not have to special-case an error for it.
    assert response.status_code == 200
    assert response.json() == {"ok": False, "cancelled": False}


def test_the_ledger_reports_nothing_rather_than_zero_when_there_is_nothing(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    summary = client.get("/api/orchestration/delegations").json()["summary"]

    assert summary["n"] == 0
    # Null, never 0.0: "no receipts carry a price" and "the hierarchy saved nothing" are different
    # claims, and rendering the first as $0.00 invents a measurement.
    assert summary["usd_saving"] is None
    assert summary["token_saving"] is None


def test_the_ledger_adds_up_what_a_real_run_wrote(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    client.post("/api/orchestration/hierarchy", json={"task": _READ_TASK})
    summary = client.get("/api/orchestration/delegations").json()["summary"]

    assert summary["n"] > 0
    assert summary["measured_tokens"] > 0
    assert summary["by_tier"]


def test_the_frame_shapes_are_published_for_the_generated_client(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    shape = client.get("/api/orchestration/schema").json()

    # An SSE route cannot declare a response_model, so this is how the payload types reach the
    # schema at all. Empty and side-effect-free: a shape sample, never fabricated results.
    assert set(shape) == {
        "classified",
        "decomposed",
        "worker_started",
        "worker_verified",
        "worker_rejected",
        "fell_back",
        "done",
    }
    assert shape["done"]["answer"] == ""
    assert shape["decomposed"]["specs"] == []
