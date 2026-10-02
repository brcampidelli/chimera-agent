"""The desktop's screen layout is kept by the server, so a reinstall does not lose it.

Phase 6 of the dynamic screen the owner approved on 2026-09-29. The layout (what is hidden, minimised,
moved, how wide) lived only in the webview's storage, which a reinstall or a cleared WebView2 profile
wipes. It now also lives in a file under CHIMERA_HOME, beside the project list, which made the same
move earlier for the same reason.

The server keeps it opaque: the client owns the model and reads anything it does not recognise as its
default, so the server checks only that it is an object, that it is small, and that no write is ever
half-done.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.ui_layout import MAX_BYTES, UiLayoutStore
from chimera.interface import ChatSession


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")

    class _Agent:
        def run(self, task: str, **_kw: Any) -> Any:
            raise AssertionError("no turn runs in this file")

    return TestClient(build_api_app(lambda: ChatSession(_Agent()), workspace=tmp_path, settings=settings))


LAYOUT = {"version": 1, "regions": {"left": {"visible": False, "size": 300}}, "maximized": None}


# ------------------------------------------------------------------------------------ the store


def test_nothing_is_stored_until_something_is_written(tmp_path: Path) -> None:
    assert UiLayoutStore(tmp_path / "ui_layout.json").read() is None


def test_what_is_written_is_what_is_read(tmp_path: Path) -> None:
    store = UiLayoutStore(tmp_path / "home" / "ui_layout.json")

    store.write(LAYOUT)

    assert store.read() == LAYOUT


def test_an_unreadable_or_non_object_file_is_no_answer_rather_than_an_error(tmp_path: Path) -> None:
    path = tmp_path / "ui_layout.json"
    store = UiLayoutStore(path)

    path.write_text("{not json", encoding="utf-8")
    assert store.read() is None
    path.write_text(json.dumps(["a", "list"]), encoding="utf-8")
    assert store.read() is None


def test_only_an_object_under_the_size_cap_is_written(tmp_path: Path) -> None:
    store = UiLayoutStore(tmp_path / "ui_layout.json")

    with pytest.raises(ValueError):
        store.write(["not", "an", "object"])
    with pytest.raises(OverflowError):
        store.write({"padding": "x" * MAX_BYTES})

    assert store.read() is None


def test_a_write_replaces_the_file_whole_and_leaves_no_temporary_behind(tmp_path: Path) -> None:
    path = tmp_path / "ui_layout.json"
    store = UiLayoutStore(path)

    store.write(LAYOUT)
    store.write({"version": 1})

    assert store.read() == {"version": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["ui_layout.json"]


# ------------------------------------------------------------------------------------ the routes


def test_the_first_read_is_null_and_a_put_is_read_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)

    assert client.get("/api/ui/layout").json() == {"layout": None}

    put = client.put("/api/ui/layout", json={"layout": LAYOUT})
    assert put.status_code == 200 and put.json() == {"layout": LAYOUT}
    assert client.get("/api/ui/layout").json() == {"layout": LAYOUT}
    assert (tmp_path / "home" / "ui_layout.json").is_file()


def test_a_layout_over_the_cap_is_refused_and_the_stored_one_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.put("/api/ui/layout", json={"layout": LAYOUT})

    big = client.put("/api/ui/layout", json={"layout": {"padding": "x" * MAX_BYTES}})

    assert big.status_code == 413
    assert client.get("/api/ui/layout").json() == {"layout": LAYOUT}


def test_a_layout_that_is_not_an_object_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)

    assert client.put("/api/ui/layout", json={"layout": ["a", "list"]}).status_code == 422
    assert client.put("/api/ui/layout", json={}).status_code == 422
