"""Whether the agent's read tools may read Chimera's own `.env` is the owner's choice.

The owner's decision of 2026-10-04: reading stays ALLOWED by default — today's behaviour — and
``CHIMERA_AGENT_READS_OWN_ENV`` (a row on the privacy card) lets the owner switch it off. On, the
provider keys saved there can reach the model; off, every read path of the agent refuses or leaves
out that one file — ``read_file``, ``grep`` (rooted at the folder or at the file), ``glob``,
``list_dir``, the document reader, and the explorer, which is built from the same tools — and it is
recognised by identity, so no other spelling of it gets through. Other projects' ``.env`` files are
unaffected. The bridge may not flip it: turning it back on loosens privacy.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import get_settings
from chimera.core.explorer import read_only_registry
from chimera.tools.files import ListDirTool, ReadFileTool
from chimera.tools.search import GlobTool, GrepTool
from chimera.tools.workspace import PathEscapesWorkspaceError

KEY = "sk-or-v1-" + "c0ffee" * 8


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reads: str | None) -> Path:
    monkeypatch.chdir(tmp_path)  # Chimera's own .env is the one in the working directory
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    if reads is None:
        monkeypatch.setenv("CHIMERA_AGENT_READS_OWN_ENV", "")
        monkeypatch.delenv("CHIMERA_AGENT_READS_OWN_ENV")
    else:
        monkeypatch.setenv("CHIMERA_AGENT_READS_OWN_ENV", reads)
    get_settings.cache_clear()
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={KEY}\n", encoding="utf-8")
    other = tmp_path / "web"
    other.mkdir()
    (other / ".env").write_text("DATABASE_URL=postgres://other\n", encoding="utf-8")
    return tmp_path


def _out(call: Any) -> str:
    try:
        return str(call())
    except PathEscapesWorkspaceError as exc:
        return f"error: {exc}"


def test_by_default_the_agent_reads_it_as_it_always_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _install(tmp_path, monkeypatch, reads=None)
    assert get_settings().agent_reads_own_env is True
    assert KEY in ReadFileTool(ws).run(path=".env")
    assert KEY in GrepTool(ws).run(pattern="sk-or-v1")
    assert ".env" in ListDirTool(ws).run(path=".")
    get_settings.cache_clear()


def test_off_every_read_path_refuses_or_leaves_it_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _install(tmp_path, monkeypatch, reads="false")
    outputs = {
        "read_file": _out(lambda: ReadFileTool(ws).run(path=".env")),
        "grep at the folder": _out(lambda: GrepTool(ws).run(pattern="sk-or-v1")),
        "grep at the file": _out(lambda: GrepTool(ws).run(pattern="sk-or-v1", path=".env")),
        "glob": _out(lambda: GlobTool(ws).run(pattern="**/.env*")),
        "list_dir": _out(lambda: ListDirTool(ws).run(path=".")),
    }
    for name, text in outputs.items():
        assert KEY not in text, name
    assert outputs["read_file"].startswith("error") and "owner" in outputs["read_file"]
    assert ".env" not in outputs["list_dir"].split("\n")
    assert ".env" not in outputs["glob"].split("\n")
    # The other project's .env is still there for every tool.
    assert "web/.env" in outputs["glob"]
    assert "postgres://other" in ReadFileTool(ws).run(path="web/.env")
    # The explorer is built from the same tools.
    explorer = read_only_registry(ws)
    assert KEY not in _out(lambda: explorer.get("read_file").run(path=".env"))
    assert KEY not in _out(lambda: explorer.get("grep").run(pattern="sk-or-v1"))
    get_settings.cache_clear()


def test_off_the_document_reader_refuses_it_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.tools.documents import ReadDocumentTool

    ws = _install(tmp_path, monkeypatch, reads="false")
    out = _out(lambda: ReadDocumentTool(ws).run(path=".env"))
    assert KEY not in out
    assert out.startswith("error") and "owner keeps it" in out, out
    get_settings.cache_clear()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spellings of one file")
@pytest.mark.parametrize("spelling", [".env ", ".env.", ".env::$DATA", ".ENV", "ENV~1"])
def test_off_no_spelling_of_the_file_gets_through(
    spelling: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _install(tmp_path, monkeypatch, reads="false")
    if spelling == "ENV~1" and not (ws / spelling).exists():
        pytest.skip("this volume keeps no 8.3 short names")
    assert KEY not in _out(lambda: ReadFileTool(ws).run(path=spelling))
    assert KEY not in _out(lambda: GrepTool(ws).run(pattern="sk-or-v1", path=spelling))
    get_settings.cache_clear()


def test_the_bridge_cannot_flip_it_and_the_owner_can(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_the_desktop_bridge_reaches_only_its_table import _app, _call

    monkeypatch.setenv("CHIMERA_AGENT_READS_OWN_ENV", "false")
    app = _app(tmp_path, monkeypatch, full=True)
    with TestClient(app) as client:
        refused = _call(client, app, "settings.edit", body={"CHIMERA_AGENT_READS_OWN_ENV": "true"})
        shown = client.get("/api/config").json()["privacy"]["agent_reads_own_env"]
        owner = client.patch("/api/config", json={"CHIMERA_AGENT_READS_OWN_ENV": "true"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "not editable through the bridge: CHIMERA_AGENT_READS_OWN_ENV"
    assert shown is False
    assert owner.status_code == 200
    get_settings.cache_clear()
