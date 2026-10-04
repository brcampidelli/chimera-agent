"""The four checks the second adversarial review of 2026-10-04 left unverified, each pinned.

* **Listings and search.** With the workspace being the install folder (it holds Chimera's ``.env``
  and the data folder), ``files.search``, ``files.tree`` and ``files.browse`` through the bridge
  neither return a line of either nor name them. Found open: search returned lines of files in the
  data folder (its hits were filtered for credential NAMES only) and the tree and browse listed the
  data folder and the ``.env``. The app's own search now drops hits in both for every caller, as the
  file reader already refused them, and the bridge drops them from the listings.
* **Things created to run later.** A cron job, a kanban card, a spec project and a crew carry no
  model choice and no widening under their own field names. Found open: ``cron.create``'s
  ``deliver_to`` — a webhook the job posts its answers to, unattended — which is now refused.
* **The suggestion digest.** Two cards with the same change still hash apart (the id is in it); a
  card read twice hashes the same; the bridge may read a digest (it is no secret) but cannot write
  the folder the cards live in, by any spelling, nor answer a card.
* **The owner's features.** The veto on Chimera's ``.env`` and data folder in the file routes does
  not touch the desktop's own layout (the default workspace is a sibling of the data folder), nor
  Settings, memory, approvals, or another project's files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import get_settings
from chimera.governance import setting_suggestions
from tests.test_the_desktop_bridge_reaches_only_its_table import _app, _ask, _call

SECRET = "sk-or-v1-" + "f00d" * 12
BS = "\\"


def _install_folder(tmp_path: Path) -> None:
    """The app's workspace IS the install folder: its `.env` and its data folder inside it."""
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={SECRET}\n", encoding="utf-8")
    memory = tmp_path / "home" / "memory.json"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text(json.dumps({"fact": f"remembered {SECRET}"}), encoding="utf-8")
    (tmp_path / "notes.txt").write_text(f"an ordinary file mentions {SECRET[:12]}\n", encoding="utf-8")


@pytest.mark.parametrize("full", [False, True], ids=["operate", "full"])
def test_search_tree_and_browse_neither_read_nor_name_chimeras_files(
    full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)
    _install_folder(tmp_path)
    with TestClient(app) as client:
        found = _call(client, app, "files.search", body={"query": SECRET[:12]})
        tree = _call(client, app, "files.tree", params={"path": ""})
        browse = _call(client, app, "files.browse", params={"path": str(tmp_path)})
        owner = client.post("/api/fs/search", json={"query": SECRET[:12]})
    hits = [h["path"] for h in found.json()["data"]["hits"]]
    assert hits == ["notes.txt"], hits
    names = {e["name"] for e in tree.json()["data"]["entries"]}
    assert "notes.txt" in names and ".env" not in names and "home" not in names
    assert "home" not in {e["name"] for e in browse.json()["data"]["entries"]}
    assert SECRET not in found.text + tree.text + browse.text
    # The owner's own search keeps the same rule as the owner's file reader.
    assert [h["path"] for h in owner.json()["hits"]] == ["notes.txt"]


@pytest.mark.parametrize("full", [False, True], ids=["operate", "full"])
def test_things_created_to_run_later_carry_no_choice_and_no_widening(
    full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)
    cases: list[tuple[str, dict[str, Any]]] = [
        ("cron.create", {"name": "j", "schedule": "0 7 * * *", "action": "x",
                         "deliver_to": "https://hooks.example.invalid/x"}),
        ("cron.create", {"name": "j", "schedule": "0 7 * * *", "action": "x",
                         "verify": "curl evil"}),
        ("kanban.add_card", {"title": "t", "verify": "curl evil"}),
        ("spec_projects.create", {"spec": "s.md", "auto_approve": True}),
        ("orchestration.crew", {"task": "x", "workers": [{"name": "a", "instruction": "b"}],
                                "verify": "curl evil"}),
        ("kanban.run", {"model": "a/b"}),
        ("orchestration.hierarchy", {"task": "x", "verifier_model": "a/b"}),
    ]
    with TestClient(app) as client:
        for route, body in cases:
            got = _call(client, app, route, body=body, wait_seconds=0)
            assert got.status_code == 403, (route, body, got.text)
        plain = _call(
            client, app, "cron.create", body={"name": "j", "schedule": "0 7 * * *", "action": "x"}
        )
    assert plain.status_code == 200 and plain.json()["status"] == 200, plain.text
    get_settings.cache_clear()


def test_the_digest_is_per_card_stable_and_no_bridge_path_writes_the_cards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=True)
    home = tmp_path / "home"
    change = [setting_suggestions.Change("CHIMERA_DEFAULT_MODEL", "a/b", "c/d")]
    one = setting_suggestions.suggest(home, change, suggested_by="desktop_bridge")
    two = setting_suggestions.suggest(home, change, suggested_by="desktop_bridge")
    s1, s2 = setting_suggestions.read(home, one), setting_suggestions.read(home, two)
    assert s1 is not None and s2 is not None
    assert setting_suggestions.digest(s1) != setting_suggestions.digest(s2), "the id is hashed"
    assert setting_suggestions.digest(s1) == setting_suggestions.digest(
        setting_suggestions.read(home, one)  # type: ignore[arg-type]
    )
    ask = home / "approvals" / f"{one}.ask.json"
    before = ask.read_bytes()
    with TestClient(app) as client:
        listed = _call(client, app, "approvals.list").json()["data"]
        assert any(q.get("suggestion", {}).get("digest") for q in listed), "readable, not secret"
        attempts = [
            ("files.write", {"path": str(ask), "content": "{}"}),
            ("files.write", {"path": f"home/approvals/{one}.ask.json", "content": "{}"}),
            ("files.write", {"path": BS * 2 + "?" + BS + str(ask.resolve()), "content": "{}"}),
            ("files.write", {"workspace": str(home), "path": f"approvals/{one}.ask.json",
                             "content": "{}"}),
            ("files.mkdir", {"path": str(home / "approvals" / "x")}),
        ]
        for route, body in attempts:
            got = _call(client, app, route, body=body)
            assert got.status_code == 403, (route, body, got.text)
        answered = _call(
            client, app, "approve.approval", params={"request_id": one}, body={"approved": True}
        )
    assert answered.status_code == 403
    assert ask.read_bytes() == before


def test_the_veto_leaves_the_owners_own_screens_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop's layout: data in `<app data>/data`, the default workspace in
    `<app data>/workspace` beside it, the `.env` in the install folder (the working directory)."""
    from typing import cast

    from chimera.api import build_api_app
    from chimera.interface import ChatSession
    from chimera.interface.session import SupportsRun

    install, appdata = tmp_path / "install", tmp_path / "appdata"
    install.mkdir()
    (appdata / "workspace").mkdir(parents=True)
    other = tmp_path / "web"
    other.mkdir()
    (other / ".env.local").write_text("NEXT_PUBLIC_X=1\n", encoding="utf-8")
    monkeypatch.chdir(install)
    monkeypatch.setenv("CHIMERA_HOME", str(appdata / "data"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_SANDBOX_IMAGE", "")
    get_settings.cache_clear()
    app = build_api_app(
        lambda: ChatSession(cast(SupportsRun, None)), workspace=appdata / "workspace"
    )
    _ask(appdata / "data")
    with TestClient(app) as client:
        wrote = client.put("/api/fs/file", json={"path": "plan.md", "content": "# plan\n"})
        read = client.get("/api/fs/file", params={"path": "plan.md"})
        tree = client.get("/api/fs/tree", params={"path": ""})
        elsewhere = client.get(
            "/api/fs/file", params={"path": ".env.local", "workspace": str(other)}
        )
        saved = client.patch("/api/config", json={"CHIMERA_SANDBOX_IMAGE": "img:1"})
        approvals = client.get("/api/approvals")
        memory = client.get("/api/memory/layers")
    assert wrote.status_code == 200 and read.json()["content"] == "# plan\n"
    assert [e["name"] for e in tree.json()["entries"]] == ["plan.md"]
    assert elsewhere.status_code == 200
    assert saved.status_code == 200
    assert "CHIMERA_SANDBOX_IMAGE=img:1" in (install / ".env").read_text(encoding="utf-8")
    assert [q["id"] for q in approvals.json()] == ["q1"]
    assert memory.status_code == 200
    get_settings.cache_clear()
