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


def _names(response: Any) -> list[dict[str, str]]:
    """Path and alias only. The rows also carry the grant, the pin, the recency and the hidden
    flag since study 29 (P4.3); the tests in this block are about the bookmark and its name, and
    `test_a_new_row_decides_nothing` below pins the rest of the shape on its own."""
    return [{"path": row["path"], "alias": row["alias"]} for row in response.json()]


def test_a_fresh_install_has_no_projects(client: TestClient) -> None:
    response = client.get("/api/code/workspaces")
    assert response.status_code == 200
    assert response.json() == []


def test_registering_a_project_lists_it(client: TestClient) -> None:
    response = client.post("/api/code/workspaces", json={"path": LOJA, "alias": "a loja"})
    assert response.status_code == 200
    assert _names(response) == [{"path": LOJA, "alias": "a loja"}]
    assert _names(client.get("/api/code/workspaces")) == [{"path": LOJA, "alias": "a loja"}]


def test_a_project_survives_without_ever_being_worked_in(client: TestClient) -> None:
    """The whole point of the route: a bookmark exists before any conversation does, which is
    exactly what the grouping derived from conversations could never express."""
    client.post("/api/code/workspaces", json={"path": LOJA})
    assert client.get("/api/code/sessions").json() == []
    assert _paths(client.get("/api/code/workspaces")) == [LOJA]


def test_re_registering_without_an_alias_field_keeps_the_name(client: TestClient) -> None:
    client.post("/api/code/workspaces", json={"path": LOJA, "alias": "a loja"})
    again = client.post("/api/code/workspaces", json={"path": LOJA})
    assert _names(again) == [{"path": LOJA, "alias": "a loja"}]


def test_an_empty_alias_clears_the_name(client: TestClient) -> None:
    client.post("/api/code/workspaces", json={"path": LOJA, "alias": "a loja"})
    cleared = client.post("/api/code/workspaces", json={"path": LOJA, "alias": ""})
    assert _names(cleared) == [{"path": LOJA, "alias": ""}]


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
    assert _names(client.get("/api/code/workspaces")) == [{"path": LOJA, "alias": "a loja"}]


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
    assert _names(again.get("/api/code/workspaces")) == [{"path": BLOG, "alias": "o blog"}]


# ------------------------------------------------- folders: the grant, the pin, hiding (P4.3)


def test_a_new_row_decides_nothing(client: TestClient) -> None:
    """Registering is a bookmark and nothing more: no grant, no pin, not hidden, never used."""
    response = client.post("/api/code/workspaces", json={"path": LOJA})
    assert response.json() == [
        {
            "path": LOJA,
            "alias": "",
            "shell_granted": False,
            "granted_at": "",
            "pinned": False,
            "last_used_at": "",
            "hidden": False,
        }
    ]


def test_granting_records_when_and_revoking_forgets_it(client: TestClient) -> None:
    granted = client.put("/api/code/workspaces/grant", json={"path": LOJA, "shell_granted": True})
    assert granted.status_code == 200
    row = granted.json()[0]
    assert row["path"] == LOJA and row["shell_granted"] is True and row["granted_at"]

    revoked = client.put("/api/code/workspaces/grant", json={"path": LOJA, "shell_granted": False})
    assert revoked.json()[0]["shell_granted"] is False and revoked.json()[0]["granted_at"] == ""


def test_naming_a_granted_project_keeps_the_grant(client: TestClient) -> None:
    """The register route predates the grant. Re-registering to rename must not quietly revoke."""
    client.put("/api/code/workspaces/grant", json={"path": LOJA, "shell_granted": True})
    renamed = client.post("/api/code/workspaces", json={"path": LOJA, "alias": "a loja"})
    assert renamed.json()[0]["alias"] == "a loja" and renamed.json()[0]["shell_granted"] is True


def test_hiding_keeps_the_row_and_revokes_the_grant(client: TestClient) -> None:
    """"Remove to never list": kept as a row, so the sidebar can leave out a folder it would
    otherwise bring back from its conversations — and the grant goes, because a permission nobody
    can see in a list is one nobody takes back."""
    client.put("/api/code/workspaces/grant", json={"path": LOJA, "shell_granted": True})
    client.patch("/api/code/workspaces", json={"path": LOJA, "pinned": True})
    hidden = client.patch("/api/code/workspaces", json={"path": LOJA, "hidden": True})
    row = hidden.json()[0]
    assert (row["hidden"], row["shell_granted"], row["pinned"]) == (True, False, False)
    assert _paths(client.get("/api/code/workspaces")) == [LOJA]


def test_pinning_a_folder_nobody_registered_registers_it(client: TestClient) -> None:
    """The sidebar lists folders from conversations too; pinning one of those must just work."""
    pinned = client.patch("/api/code/workspaces", json={"path": BLOG, "pinned": True})
    assert pinned.json()[0]["path"] == BLOG and pinned.json()[0]["pinned"] is True


def test_the_migration_records_the_old_grants_once(client: TestClient) -> None:
    """The desktop's grants lived in its own storage. They cross once; after that the client's word
    is not a grant, so a second call — with anything in it — changes nothing."""
    first = client.post("/api/code/workspaces/grant/migrate", json={"paths": [LOJA, BLOG]})
    assert first.status_code == 200
    body = first.json()
    assert body["migrated"] is True and body["recorded"] == 2
    assert {r["path"] for r in body["projects"] if r["shell_granted"]} == {LOJA, BLOG}

    second = client.post("/api/code/workspaces/grant/migrate", json={"paths": ["C:\\outra"]})
    assert second.json()["migrated"] is False and second.json()["recorded"] == 0
    assert "C:\\outra" not in _paths(client.get("/api/code/workspaces"))


def test_an_empty_migration_still_closes_the_window(client: TestClient) -> None:
    """A fresh install has nothing to move, and must not leave the door open for a later claim."""
    assert client.post("/api/code/workspaces/grant/migrate", json={"paths": []}).json()["migrated"]
    late = client.post("/api/code/workspaces/grant/migrate", json={"paths": [LOJA]}).json()
    assert late["migrated"] is False
    assert client.get("/api/code/workspaces").json() == []


def test_the_posture_sentence_is_held_to_the_grant(client: TestClient, tmp_path: Path) -> None:
    """The screen's posture line describes the run that happens. A `workspace_shell` asked for in
    a folder nobody granted is described as the run it will be: no shell."""
    pasta = tmp_path / "ws"
    asked = {"reach": "workspace_shell", "approval": "never", "workspace": str(pasta)}
    before = client.post("/api/code/posture", json=asked).json()
    client.put("/api/code/workspaces/grant", json={"path": str(pasta), "shell_granted": True})
    after = client.post("/api/code/posture", json=asked).json()
    assert before["shell"] == "none"
    assert after["shell"] != "none"


def test_a_turn_stamps_the_last_use_of_a_registered_folder(tmp_path: Path) -> None:
    """Only a registered folder, and only when a turn actually starts there."""
    from chimera.core.code_projects import CodeProjectRegistry

    registry = CodeProjectRegistry(tmp_path / "home" / "code_projects.json")
    pasta = tmp_path / "loja"
    pasta.mkdir()
    registry.register(str(pasta))
    registry.touch(str(pasta))
    registry.touch(str(tmp_path / "nunca-registrada"))
    rows = registry.entries()
    assert [r.path for r in rows] == [str(pasta)]
    assert rows[0].last_used_at
