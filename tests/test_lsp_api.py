"""`POST /api/lsp/diagnostics` — the boundary between the editor and a language server.

What these hold to account is not the protocol (that is `test_lsp_client.py`, against the real
binary) but the endpoint's honesty: that an empty list of problems is never confused with a machine
where nothing looked, and that a request carrying an unsaved buffer diagnoses the buffer.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402

HAS_RUFF = shutil.which("ruff") is not None


class _Idle:
    run_state = RunState()

    def run(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("these tests never run the agent")


@pytest.fixture()
def client(tmp_path: Path):
    from chimera.api import build_api_app
    from chimera.api.lsp_api import close_all

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "pyproject.toml").write_text(
        '[tool.ruff]\n[tool.ruff.lint]\nselect = ["E", "F"]\n', encoding="utf-8"
    )
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    yield TestClient(build_api_app(lambda: ChatSession(_Idle()), workspace=ws, settings=settings)), ws
    close_all()


@pytest.mark.skipif(not HAS_RUFF, reason="ruff not on PATH")
def test_it_diagnoses_the_buffer_not_the_file_on_disk(client) -> None:
    """The whole reason the text travels with the request.

    Diagnosing the saved copy means every squiggle is one save behind — pointing at problems the
    user already fixed, which is worse than showing none.
    """
    api, ws = client
    (ws / "app.py").write_text("x = 1\n", encoding="utf-8")  # clean ON DISK

    body = api.post(
        "/api/lsp/diagnostics", json={"path": "app.py", "text": "import os\n\nx = 1\n"}
    ).json()

    assert body["available"] is True
    assert any(d["code"] == "F401" for d in body["diagnostics"])


@pytest.mark.skipif(not HAS_RUFF, reason="ruff not on PATH")
def test_a_corrected_buffer_stops_reporting_the_problem_it_had(client) -> None:
    """The second request for the SAME file must diagnose the second text.

    This shipped broken and the whole suite was green, because every test asked once — the one that
    asked twice used two different paths. The endpoint polled its own diagnostics cache and stopped
    as soon as it was non-empty, which after the first request is immediately, so the second answer
    was the first answer. Symptom: you delete the unused import, the squiggle stays, and the editor
    is lying about the file in front of you — the one failure this feature exists to prevent.
    """
    api, _ = client

    first = api.post("/api/lsp/diagnostics", json={"path": "a.py", "text": "import os\n"}).json()
    assert [d["code"] for d in first["diagnostics"]] == ["F401"]

    fixed = api.post("/api/lsp/diagnostics", json={"path": "a.py", "text": "x = 1\n"}).json()

    assert fixed["available"] is True
    assert fixed["diagnostics"] == [], "the editor kept the diagnostics of a buffer that is gone"


@pytest.mark.skipif(not HAS_RUFF, reason="ruff not on PATH")
def test_a_new_problem_in_the_same_file_is_reported(client) -> None:
    # The other direction of the same bug: going clean → broken must also arrive.
    api, _ = client
    api.post("/api/lsp/diagnostics", json={"path": "b.py", "text": "x = 1\n"})

    body = api.post("/api/lsp/diagnostics", json={"path": "b.py", "text": "import sys\n"}).json()

    assert [d["code"] for d in body["diagnostics"]] == ["F401"]


@pytest.mark.skipif(not HAS_RUFF, reason="ruff not on PATH")
def test_a_clean_buffer_is_available_and_empty(client) -> None:
    # The pair that must never collapse into one: nothing wrong, and something looked.
    api, _ = client
    body = api.post("/api/lsp/diagnostics", json={"path": "ok.py", "text": "x = 1\n"}).json()

    assert body["available"] is True
    assert body["diagnostics"] == []
    assert body["note"] == ""


def test_a_missing_language_server_says_so_rather_than_looking_clean(
    client, monkeypatch: Any
) -> None:
    """`available: false` with a note, never an empty list.

    An editor that showed no squiggles either way would be reporting a clean bill of health nobody
    checked — and the note carries the install line, because "unavailable" without a remedy is a
    shrug.
    """
    monkeypatch.setattr("chimera.api.lsp_api.ruff_available", lambda: False)
    api, _ = client

    body = api.post("/api/lsp/diagnostics", json={"path": "app.py", "text": "import os\n"}).json()

    assert body["available"] is False
    assert body["diagnostics"] == []
    assert "ruff" in body["note"]


def test_a_non_python_file_is_declined_out_loud(client) -> None:
    # ruff has no opinion about a .ts file, and a silent empty answer there reads as "this is fine".
    api, _ = client
    body = api.post("/api/lsp/diagnostics", json={"path": "app.ts", "text": "const x = 1;"}).json()

    assert body["available"] is False
    assert "Python" in body["note"]


def test_a_path_outside_the_workspace_is_refused(client) -> None:
    """The same jail as every other fs route. A diagnostics endpoint that reads any path is a file
    reader with a language server attached."""
    api, _ = client
    body = api.post(
        "/api/lsp/diagnostics", json={"path": "../../secrets.py", "text": "x = 1\n"}
    ).json()

    assert body["available"] is False
    assert "outside" in body["note"]


@pytest.mark.skipif(not HAS_RUFF, reason="ruff not on PATH")
def test_the_server_is_reused_between_requests(client) -> None:
    """Starting one costs a spawn and a handshake — nothing once, everything per keystroke."""
    from chimera.api.lsp_api import _servers

    api, _ = client
    api.post("/api/lsp/diagnostics", json={"path": "a.py", "text": "import os\n"})
    first = {key: id(value[0]) for key, value in _servers.items()}
    api.post("/api/lsp/diagnostics", json={"path": "b.py", "text": "import sys\n"})
    second = {key: id(value[0]) for key, value in _servers.items()}

    assert first and first == second, "the second request started a second language server"


@pytest.mark.skipif(not HAS_RUFF, reason="ruff not on PATH")
def test_closing_leaves_nothing_running(client) -> None:
    from chimera.api.lsp_api import _servers, close_all

    api, _ = client
    api.post("/api/lsp/diagnostics", json={"path": "a.py", "text": "import os\n"})
    assert _servers

    close_all()

    assert not _servers
