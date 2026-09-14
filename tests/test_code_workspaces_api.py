"""Registering a project over HTTP, and the one route pair that must never be confused.

`DELETE /api/code/projects` deletes every CONVERSATION filed under a workspace.
`DELETE /api/code/workspaces` forgets a BOOKMARK and touches nothing else.

They are one word apart, they take the same kind of argument, and one of them destroys work. So the
test that matters most here is the one that registers a project, deletes the registration, and shows
the transcripts still listed — because "tidy up my list" losing a month of conversations is not a
behaviour that gets a second chance to be right.

The other thing only an HTTP test can show is **absent against empty**: the registry keeps them
apart, but JSON is where they stop looking different, so re-registering a named project without an
alias field has to be proven to keep the name here rather than only in the unit test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.interface.session import ChatSession  # noqa: E402

LOJA = "C:\\Users\\alguem\\loja"
BLOG = "C:\\Users\\alguem\\blog"


class _MudoAgent:
    """Never called: this file exercises the registry routes, which build no agent."""

    def run(self, task: str, **_kw: Any) -> Any:  # pragma: no cover - defensive
        raise AssertionError("no turn should run in this file")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(
        build_api_app(lambda: ChatSession(_MudoAgent()), workspace=ws, settings=settings)
    )


def _paths(response: Any) -> list[str]:
    return [row["path"] for row in response.json()]


def test_a_fresh_install_has_no_projects(client: TestClient) -> None:
    response = client.get("/api/code/workspaces")
    assert response.status_code == 200
    assert response.json() == []


def test_registering_a_project_lists_it(client: TestClient) -> None:
    response = client.post("/api/code/workspaces", json={"path": LOJA, "alias": "a loja"})
    assert response.status_code == 200
    assert response.json() == [{"path": LOJA, "alias": "a loja"}]
    assert client.get("/api/code/workspaces").json() == [{"path": LOJA, "alias": "a loja"}]


def test_a_project_survives_without_ever_being_worked_in(client: TestClient) -> None:
    """The whole point of the route: a bookmark exists before any conversation does, which is
    exactly what the grouping derived from conversations could never express."""
    client.post("/api/code/workspaces", json={"path": LOJA})
    assert client.get("/api/code/sessions").json() == []
    assert _paths(client.get("/api/code/workspaces")) == [LOJA]


def test_re_registering_without_an_alias_field_keeps_the_name(client: TestClient) -> None:
    client.post("/api/code/workspaces", json={"path": LOJA, "alias": "a loja"})
    again = client.post("/api/code/workspaces", json={"path": LOJA})
    assert again.json() == [{"path": LOJA, "alias": "a loja"}]


def test_an_empty_alias_clears_the_name(client: TestClient) -> None:
    client.post("/api/code/workspaces", json={"path": LOJA, "alias": "a loja"})
    cleared = client.post("/api/code/workspaces", json={"path": LOJA, "alias": ""})
    assert cleared.json() == [{"path": LOJA, "alias": ""}]


def test_a_blank_path_is_refused_rather_than_stored(client: TestClient) -> None:
    response = client.post("/api/code/workspaces", json={"path": "   "})
    assert response.status_code == 400
    assert client.get("/api/code/workspaces").json() == []


def test_forgetting_a_bookmark_keeps_every_conversation(client: TestClient, tmp_path: Path) -> None:
    """The one that matters. One word separates this route from the one that deletes transcripts."""
    sessions = tmp_path / "home" / "code_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "s1.json").write_text(
        json.dumps({"session_id": "s1", "workspace": LOJA,
                    "messages": [{"role": "user", "content": "oi"}]}),
        encoding="utf-8",
    )
    client.post("/api/code/workspaces", json={"path": LOJA})

    response = client.request("DELETE", "/api/code/workspaces", params={"path": LOJA})

    assert response.status_code == 200
    assert response.json() == []
    assert (sessions / "s1.json").exists()
    assert [row["workspace"] for row in client.get("/api/code/sessions").json()] == [LOJA]


def test_deleting_the_conversations_leaves_the_bookmark(client: TestClient, tmp_path: Path) -> None:
    """And the same statement from the other side: the older route removes transcripts and says
    nothing about the list, so a project you emptied is still a project you work in."""
    sessions = tmp_path / "home" / "code_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "s1.json").write_text(
        json.dumps({"session_id": "s1", "workspace": LOJA,
                    "messages": [{"role": "user", "content": "oi"}]}),
        encoding="utf-8",
    )
    client.post("/api/code/workspaces", json={"path": LOJA, "alias": "a loja"})

    removed = client.request("DELETE", "/api/code/projects", params={"workspace": LOJA})

    assert removed.json() == {"deleted": 1}
    assert client.get("/api/code/workspaces").json() == [{"path": LOJA, "alias": "a loja"}]


def test_the_list_survives_a_restart(client: TestClient, tmp_path: Path) -> None:
    """What the move to the server was for. The file is under CHIMERA_HOME, so a second app built
    on the same home sees the same projects — which the webview's storage could not promise."""
    client.post("/api/code/workspaces", json={"path": BLOG, "alias": "o blog"})

    from chimera.api import build_api_app

    again = TestClient(
        build_api_app(
            lambda: ChatSession(_MudoAgent()),
            workspace=tmp_path / "ws",
            settings=Settings(CHIMERA_HOME=str(tmp_path / "home")),
        )
    )
    assert again.get("/api/code/workspaces").json() == [{"path": BLOG, "alias": "o blog"}]
