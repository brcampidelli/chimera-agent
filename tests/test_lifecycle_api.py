"""The SDLC lifecycle over HTTP.

`LifecycleCrew` — plan → build → test → review with verify-or-revert — has been tested and
working for a long time, and only `chimera lifecycle` in a terminal could reach it. The point of
the lifecycle over an ordinary run is that its stages are *visible*, so a screen that showed all
four at once after several minutes would have delivered `solve` with extra waiting; the route
streams a frame per stage and the crew learned to report them as they land.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.providers import CompletionResult


def _read_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[len("data:"):].strip())))
    return events


class _Backend:
    """Answers every model call with one line. The crew's four stages each make one."""

    text = "1. do the thing"

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> CompletionResult:
        return CompletionResult(
            content=type(self).text, model="fake", prompt_tokens=1, completion_tokens=1
        )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api.app import build_api_app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    from chimera.config import get_settings

    get_settings.cache_clear()
    return TestClient(
        build_api_app(  # type: ignore[arg-type]
            lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path / "home"))
        )
    )


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    folder = tmp_path / "ws"
    folder.mkdir()
    return folder


def _run(client: TestClient, **body: Any) -> list[tuple[str, dict[str, Any]]]:
    with client.stream("POST", "/api/lifecycle", json=body) as r:
        assert r.status_code == 200
        return _read_sse("".join(r.iter_text()))


def test_every_stage_gets_its_own_frame(client: TestClient, ws: Path, monkeypatch) -> None:
    """The whole reason this route exists. Four stages in one `done` payload would be the CLI's
    output pasted into a browser after several minutes of nothing."""
    monkeypatch.setattr("chimera.providers.LLMGateway", _Backend)
    events = _run(client, task="add a greeting", workspace=str(ws))

    stages = [d["name"] for e, d in events if e == "stage"]
    assert stages == ["plan", "build", "test", "review"]


def test_the_stages_arrive_before_the_verdict(client: TestClient, ws: Path, monkeypatch) -> None:
    """Ordering, not just presence. A stage frame emitted after `done` is a progress report that
    arrives once there is no longer any progress to report."""
    monkeypatch.setattr("chimera.providers.LLMGateway", _Backend)
    kinds = [e for e, _ in _run(client, task="x", workspace=str(ws))]

    assert kinds.index("done") > max(i for i, k in enumerate(kinds) if k == "stage")


def test_it_says_what_will_judge_the_run_before_it_starts(
    client: TestClient, ws: Path, monkeypatch
) -> None:
    """"No verify command — this build is judged by a model reading its own answer" has always
    been true whenever the field was empty. An interface that does not say so lets an approving
    paragraph pass for a passing test."""
    monkeypatch.setattr("chimera.providers.LLMGateway", _Backend)
    events = _run(client, task="x", workspace=str(ws))

    verify = next(d for e, d in events if e == "verify")
    assert verify["command"] == ""
    assert verify["source"]
    # And it lands before any work: the point is the warning, not the record of it.
    kinds = [e for e, _ in events]
    assert kinds.index("verify") < kinds.index("stage")


def test_a_verify_command_is_reported_as_the_gate(
    client: TestClient, ws: Path, monkeypatch
) -> None:
    """The control. If both branches printed the same thing the frame would carry no information."""
    monkeypatch.setattr("chimera.providers.LLMGateway", _Backend)
    events = _run(client, task="x", workspace=str(ws), verify="pytest -q")

    assert next(d for e, d in events if e == "verify")["command"] == "pytest -q"


def test_the_run_announces_its_id_first(client: TestClient, ws: Path, monkeypatch) -> None:
    """So Stop can target it from the first moment, rather than from whenever the first stage
    happens to finish — which on the build stage can be minutes."""
    monkeypatch.setattr("chimera.providers.LLMGateway", _Backend)
    events = _run(client, task="x", workspace=str(ws))

    assert events[0][0] == "run"
    assert events[0][1]["run_id"]
    assert events[0][1]["workspace"] == str(ws)


def test_cancelling_an_unknown_run_is_not_an_error(client: TestClient) -> None:
    """A run that already ended is exactly the state a stale Stop click lands on."""
    r = client.post("/api/lifecycle/nope/cancel")
    assert r.status_code == 200 and r.json()["ok"] is False


def test_an_empty_task_is_refused_before_the_stream_opens(client: TestClient, ws: Path) -> None:
    assert client.post("/api/lifecycle", json={"task": "  ", "workspace": str(ws)}).status_code == 400


def test_a_missing_folder_is_a_400_not_an_error_frame(client: TestClient, tmp_path: Path) -> None:
    """Once the SSE response is handed back the status is already 200, and a missing folder could
    only arrive as a failure dressed up as a run that started."""
    r = client.post("/api/lifecycle", json={"task": "x", "workspace": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_a_broken_provider_is_an_error_frame_not_a_500(
    client: TestClient, ws: Path, monkeypatch
) -> None:
    class _Broken:
        def complete(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("provider down")

    monkeypatch.setattr("chimera.providers.LLMGateway", _Broken)
    events = _run(client, task="x", workspace=str(ws))

    assert any(e == "error" for e, _ in events)


def test_the_route_governs_the_registry_it_hands_the_crew(
    client: TestClient, ws: Path, monkeypatch
) -> None:
    """`lifecycle_crew` defaults to the bare workspace registry. That is right for a terminal and
    wrong for anything reachable from a browser, so the route must pass a governed one — and this
    is the test the exemption in `test_governed_surfaces.py` now points at, because an exemption
    is prose and prose goes stale.
    """
    import chimera.api.lifecycle_api as mod
    import chimera.orchestration.lifecycle as lifecycle_mod

    seen: dict[str, Any] = {}
    real_assemble = mod.assemble_registry
    real_crew = lifecycle_mod.lifecycle_crew

    def spy_assemble(*args: Any, **kwargs: Any) -> Any:
        out = real_assemble(*args, **kwargs)
        seen["assembled"] = out[0]
        seen["surface"] = kwargs.get("surface")
        return out

    def spy_crew(*args: Any, **kwargs: Any) -> Any:
        seen["given"] = kwargs.get("registry")
        return real_crew(*args, **kwargs)

    monkeypatch.setattr(mod, "assemble_registry", spy_assemble)
    monkeypatch.setattr(lifecycle_mod, "lifecycle_crew", spy_crew)
    monkeypatch.setattr("chimera.providers.LLMGateway", _Backend)
    _run(client, task="x", workspace=str(ws))

    # Identity, not type. With governance staged off by flag, `assemble_registry` hands back an
    # ordinary ToolRegistry — so a type comparison would measure the machine's configuration
    # rather than what this route does. What the route must do is pass ALONG the assembled
    # registry, whatever the deployment turned on inside it.
    assert seen.get("assembled") is not None, "the route never assembled a registry"
    assert seen.get("given") is seen["assembled"], (
        "the crew was handed a different registry than the one the route governed"
    )
    assert seen.get("surface") == "api:lifecycle"


def test_the_stage_output_is_bounded(client: TestClient, ws: Path, monkeypatch) -> None:
    """A stage frame is a progress report, not the artefact. An unbounded one puts a whole build
    log through the queue four times."""
    monkeypatch.setattr(_Backend, "text", "x" * 20_000)
    monkeypatch.setattr("chimera.providers.LLMGateway", _Backend)
    events = _run(client, task="x", workspace=str(ws))

    assert all(len(d["output"]) <= 4000 for e, d in events if e == "stage")
