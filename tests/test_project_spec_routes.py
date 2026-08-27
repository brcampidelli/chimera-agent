"""Describing a project instead of writing its YAML.

The orchestrator is the most capable thing in the application, and its only door was a text field
asking for the path of a spec file. Everyone who cannot write that YAML was standing outside it.

Two routes, deliberately separate: drafting spends a model call and writes nothing; writing spends
nothing and puts on disk only what the person kept. The edit in between is the entire point — the
spec is the acceptance authority, so a requirement nobody understood is a project that finishes on
a condition nobody chose.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings

BOM = """{"name": "Padaria Aurora",
 "requirements": [
  {"id": "mostra-o-nome", "text": "A pagina mostra o nome da padaria.",
   "check": "contains", "target": "Padaria Aurora", "required": true},
  {"id": "mostra-o-horario", "text": "A pagina diz a que horas abrimos.",
   "check": "contains", "target": "7h", "required": true},
  {"id": "sem-lorem", "text": "Nada de texto de enchimento.",
   "check": "absent", "target": "lorem ipsum", "required": true}]}"""


class _Canned:
    """A gateway that answers with `reply` and counts how often it was asked."""

    calls = 0
    reply = BOM

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        type(self).calls += 1
        return type("R", (), {"content": type(self).reply, "usage": None})()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api.app import build_api_app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    _Canned.calls = 0
    _Canned.reply = BOM
    monkeypatch.setattr("chimera.providers.LLMGateway", _Canned)
    return TestClient(
        build_api_app(  # type: ignore[arg-type]
            lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path / "home"))
        )
    )


def test_a_description_comes_back_as_requirements(client: TestClient) -> None:
    body = client.post(
        "/api/projects/draft",
        json={"description": "um site pra minha padaria com o cardapio e o horario"},
    ).json()

    assert body["note"] == ""
    assert body["name"] == "padaria-aurora"
    assert [r["id"] for r in body["requirements"]] == [
        "mostra-o-nome", "mostra-o-horario", "sem-lorem"
    ]
    # Both halves, together: the sentence a person approves and the check that actually runs.
    assert body["requirements"][0]["text"] == "A pagina mostra o nome da padaria."
    assert body["requirements"][0]["target"] == "Padaria Aurora"


def test_drafting_writes_nothing(client: TestClient, tmp_path: Path) -> None:
    """A draft is a proposal. Writing before anyone read it would make the review theatre."""
    ws = tmp_path / "ws"
    ws.mkdir()
    client.post("/api/projects/draft", json={"description": "x", "workspace": str(ws)})
    assert list(ws.iterdir()) == []


def test_a_refused_command_is_counted_in_the_answer(client: TestClient) -> None:
    """Measured on real drafts: one in three emitted a `command` check. The screen has to be able
    to say what is no longer being verified."""
    _Canned.reply = BOM.replace('"check": "absent"', '"check": "command"')
    body = client.post("/api/projects/draft", json={"description": "x"}).json()

    assert body["refused_commands"] == 1
    assert body["refused_ids"] == ["sem-lorem"]
    assert all(r["check"] != "command" for r in body["requirements"])


def test_an_undraftable_description_is_a_sentence_not_a_500(client: TestClient) -> None:
    _Canned.reply = "desculpa, nao entendi"
    r = client.post("/api/projects/draft", json={"description": "aaa"})

    assert r.status_code == 200
    assert r.json()["requirements"] == []
    assert r.json()["note"]


def test_a_model_that_falls_over_is_a_sentence_too(client: TestClient, monkeypatch) -> None:
    class _Broken:
        def complete(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("provider down")

    monkeypatch.setattr("chimera.providers.LLMGateway", _Broken)
    r = client.post("/api/projects/draft", json={"description": "x"})

    assert r.status_code == 200 and r.json()["note"]


def test_writing_puts_the_spec_in_the_project_folder(client: TestClient, tmp_path: Path) -> None:
    """Into the folder on purpose: the spec decides when the project is done, so it belongs beside
    the code where somebody can read later what was actually agreed."""
    ws = tmp_path / "ws"
    ws.mkdir()
    draft = client.post("/api/projects/draft", json={"description": "x"}).json()

    path = Path(client.post(
        "/api/projects/spec",
        json={"name": draft["name"], "requirements": draft["requirements"], "workspace": str(ws)},
    ).json()["path"])

    assert path.parent == ws.resolve()
    assert "Padaria Aurora" in path.read_text(encoding="utf-8")


def test_writing_spends_no_model_call(client: TestClient, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    client.post(
        "/api/projects/spec",
        json={
            "name": "x",
            "requirements": [
                {"id": "a", "text": "t", "check": "contains", "target": "a", "required": True}
            ],
            "workspace": str(ws),
        },
    )
    assert _Canned.calls == 0


def test_only_what_the_person_kept_reaches_disk(client: TestClient, tmp_path: Path) -> None:
    """The route writes its argument, not the draft. If it re-read the draft, deleting a
    requirement on the screen would change nothing — and the review would be decoration."""
    ws = tmp_path / "ws"
    ws.mkdir()
    draft = client.post("/api/projects/draft", json={"description": "x"}).json()
    kept = [r for r in draft["requirements"] if r["id"] != "sem-lorem"]

    path = Path(client.post(
        "/api/projects/spec",
        json={"name": draft["name"], "requirements": kept, "workspace": str(ws)},
    ).json()["path"])

    assert "lorem" not in path.read_text(encoding="utf-8")


def test_a_command_check_is_refused_at_the_boundary_that_writes(
    client: TestClient, tmp_path: Path
) -> None:
    """Not only in the drafter. The rule has to hold where the file is created, or a client that
    edits the JSON on the way past writes a shell command into the thing that judges the project.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    r = client.post(
        "/api/projects/spec",
        json={
            "name": "x",
            "requirements": [
                {"id": "a", "text": "roda os testes", "check": "command",
                 "target": "rm -rf /", "required": True}
            ],
            "workspace": str(ws),
        },
    )

    assert r.status_code == 400
    assert list(ws.iterdir()) == []


def test_the_written_spec_starts_a_real_project(client: TestClient, tmp_path: Path) -> None:
    """The end of the road, and the only test that proves the three routes are one flow rather
    than three endpoints that happen to exist."""
    ws = tmp_path / "ws"
    ws.mkdir()
    draft = client.post("/api/projects/draft", json={"description": "x"}).json()
    path = client.post(
        "/api/projects/spec",
        json={"name": draft["name"], "requirements": draft["requirements"], "workspace": str(ws)},
    ).json()["path"]

    created = client.post(
        "/api/projects", json={"spec": path, "workspace": str(ws)}
    )

    assert created.status_code == 200
    assert created.json()["status"] == "planning"


def test_the_project_is_not_already_done_the_moment_it_starts(
    client: TestClient, tmp_path: Path
) -> None:
    """The spec now sits in the folder it judges, and every `contains` target is a regex searched
    across every text file there — including, until this was fixed, the spec's own requirement
    text. Without the exclusion this project reports `done` on its first step having written
    nothing. See `tests/test_spec_is_not_its_own_evidence.py`."""
    ws = tmp_path / "ws"
    ws.mkdir()
    draft = client.post("/api/projects/draft", json={"description": "x"}).json()
    path = client.post(
        "/api/projects/spec",
        json={"name": draft["name"], "requirements": draft["requirements"], "workspace": str(ws)},
    ).json()["path"]
    created = client.post(
        "/api/projects", json={"spec": path, "workspace": str(ws), "auto_approve": True}
    ).json()

    from chimera.governance.drift import check_drift, load_spec

    assert not check_drift(load_spec(path), ws).aligned
    assert created["status"] != "done"
