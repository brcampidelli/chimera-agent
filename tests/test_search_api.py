"""`POST /api/fs/search` and `GET /api/resources` — the two things P3 puts on screen.

The engines and the snapshot have their own tests; these hold the BOUNDARY: that a search is scoped
to the workspace like every other fs route, that a query travels in a body rather than a URL, and
that the telemetry endpoint answers on a machine that has none of the tools it would like.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _Idle:
    run_state = RunState()

    def run(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover - never reached
        raise AssertionError("these tests do not run the agent")


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "src").mkdir(exist_ok=True)
    (ws / "src" / "app.py").write_text("def connect():\n    pass\n", encoding="utf-8")
    (ws / "notes.md").write_text("connect early\n", encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    app = build_api_app(lambda: ChatSession(_Idle()), workspace=ws, settings=settings)
    return TestClient(app), ws


def test_a_search_returns_hits_with_places(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    body = client.post("/api/fs/search", json={"query": "connect"}).json()

    assert {hit["path"] for hit in body["hits"]} == {"src/app.py", "notes.md"}
    assert all(hit["line"] > 0 for hit in body["hits"])
    assert body["error"] == ""


def test_the_answer_names_the_engine(tmp_path: Path) -> None:
    # The property that makes a fallback allowed to exist at all: the screen can say the search was
    # the simpler one instead of silently returning different results.
    client, _ = _client(tmp_path)
    assert client.post("/api/fs/search", json={"query": "connect"}).json()["engine"] in (
        "ripgrep",
        "python",
    )


def test_a_search_is_scoped_to_the_workspace(tmp_path: Path) -> None:
    """The same jail as the tree and the file reader. A search that walks out of the workspace is a
    read of the whole disk behind a text box."""
    client, ws = _client(tmp_path)
    (tmp_path / "outside.txt").write_text("connect from outside\n", encoding="utf-8")

    body = client.post("/api/fs/search", json={"query": "connect"}).json()

    assert not any("outside" in hit["path"] for hit in body["hits"])
    assert not any("from outside" in hit["text"] for hit in body["hits"])


def test_naming_another_workspace_searches_that_one(tmp_path: Path) -> None:
    """`workspace` is the caller's CHOICE of project, not a path inside one.

    Written first as "a workspace that escapes is refused", which failed by returning five hundred
    hits from the parent directory — and the failure was the test's, not the endpoint's. Every fs
    route takes this parameter the same way (`_resolve_fs_workspace`): the app's folder picker sends
    an absolute path, and anyone who can call this endpoint can already call `/api/fs/exec` and run
    a shell in that folder. A search over a directory the caller named adds no reach.

    What IS a boundary is the one below: paths that come back are relative to the workspace that was
    asked about, so a hit can be opened without a second normalisation nobody remembers.
    """
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "far.txt").write_text("connect over here\n", encoding="utf-8")
    client, _ = _client(tmp_path)

    body = client.post("/api/fs/search", json={"query": "connect", "workspace": str(other)}).json()

    assert [hit["path"] for hit in body["hits"]] == ["far.txt"]


def test_a_workspace_that_is_not_a_directory_is_a_400(tmp_path: Path) -> None:
    # The same answer the tree and the file reader give, so a stale project in the picker fails the
    # same way everywhere rather than once per screen.
    client, _ = _client(tmp_path)
    response = client.post(
        "/api/fs/search", json={"query": "x", "workspace": str(tmp_path / "nope")}
    )
    assert response.status_code == 400


def test_an_empty_query_is_an_empty_answer(tmp_path: Path) -> None:
    # A search box sends one of these on the way to the first character.
    client, _ = _client(tmp_path)
    assert client.post("/api/fs/search", json={"query": ""}).json()["hits"] == []


def test_a_bad_pattern_is_reported_not_raised(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    body = client.post("/api/fs/search", json={"query": "(unclosed", "regex": True}).json()
    assert body["hits"] == []
    assert body["error"]  # the message belongs to whoever typed it, not to a 500


def test_resources_answers_on_any_machine(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    body = client.get("/api/resources").json()

    assert body["cpu_count"] >= 1
    assert set(body) == {"cpu_percent", "cpu_count", "memory", "process_mb", "gpus", "notes"}


def test_resources_keeps_absent_things_absent(tmp_path: Path, monkeypatch: Any) -> None:
    """The contract, at the boundary: nulls survive serialisation.

    A JSON layer that coerced these to 0 would undo the whole module on the way to the screen — and
    0% VRAM on an AMD card is the kind of wrong that gets believed.
    """
    monkeypatch.setattr("chimera.core.resources._psutil", lambda: None)
    monkeypatch.setattr("chimera.core.resources.shutil.which", lambda _n: None)
    client, _ = _client(tmp_path)

    body = client.get("/api/resources").json()

    assert body["cpu_percent"] is None
    assert body["memory"]["total_mb"] is None
    assert body["gpus"] == []
    assert any("psutil" in note for note in body["notes"])
    assert any("unavailable rather than zero" in note for note in body["notes"])
