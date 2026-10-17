"""A conversation is called by words someone wrote: the owner's, the caller's, the task file's, or its
first message — in that order, and never a model's (study 03, A2).

The orchestrator opens every task with the same sentence ("Leia `TAREFA.md`…"), so a list titled by
first messages read the same on every row. Three better sources sit above that rule, and each is
held here: the owner's rename outranks a title the caller passed, which outranks the heading of the
Markdown file the first message names, which outranks the first message.

The heading is read from a file the MESSAGE names, so the reader is held to the workspace: no
climbing out, no absolute path elsewhere, no link followed, a bounded read, and a heading stripped of
control and formatting characters. Each of those is tested by sabotage where it can be — the guard
switched off, the file read — so a guard that stopped working would fail a test, not pass silently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

import chimera.core.conversation_title as conversation_title
from chimera.api import build_api_app
from chimera.api.bridge_routes import ROUTES
from chimera.config import get_settings
from chimera.core.code_session import CodeSession, CodeSessionStore
from chimera.core.conversation_title import (
    HEADING_READ_BYTES,
    TITLE_MAX,
    choose_title,
    clean_title,
    first_heading,
    heading_title,
    referenced_markdown,
)
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun
from chimera.server.desktop_mcp import DesktopMCP
from tests.conversation_fakes import Agent, App


class _Agent:
    def run(self, task: str, **_kw: Any) -> Any:  # pragma: no cover - never run here
        raise AssertionError("no turn runs in these tests")


def _task(folder: Path, text: str, name: str = "TAREFA.md") -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


# ---------------------------------------------------------------------------- cleaning a title


def test_a_title_is_one_plain_line_without_control_or_formatting_characters() -> None:
    assert clean_title("  Fix\tthe\n\nlogin  ") == "Fix the login"
    # A right-to-left override would reorder the row it sits in; a zero-width space hides text.
    assert clean_title("abc‮def​ghi\x07") == "abcdefghi"
    assert clean_title("x" * 500) == "x" * TITLE_MAX
    assert clean_title(" ​\n ") == ""


def test_the_first_level_one_heading_is_the_title_and_nothing_else_is() -> None:
    text = "﻿intro\n## Not this\n```\n# not in a fence\n```\n#   Abas e divisão  ##\n# later"
    assert first_heading(text) == "Abas e divisão"
    assert first_heading("#hashtag\n##two") == ""


def test_backticked_references_come_first_and_repeat_once() -> None:
    message = "See notes.md, then read `TAREFA.md` and `docs/x.md` and notes.md again."
    assert referenced_markdown(message) == ["TAREFA.md", "docs/x.md", "notes.md"]


@pytest.mark.parametrize(
    ("owner", "stamped", "source", "expected"),
    [
        ("Mine", "Caller's", "caller", ("Mine", "owner")),
        ("", "Caller's", "caller", ("Caller's", "caller")),
        ("", "Heading", "heading", ("Heading", "heading")),
        ("", "Forged", "model", ("first ask", "message")),
        ("", "", "", ("first ask", "message")),
    ],
)
def test_the_precedence_is_owner_caller_heading_message(
    owner: str, stamped: str, source: str, expected: tuple[str, str]
) -> None:
    assert (
        choose_title(owner=owner, stamped=stamped, stamped_source=source, derived="first ask")
        == expected
    )


# ---------------------------------------------------------------------------- reading the heading


def test_the_heading_of_the_named_task_file_is_read(tmp_path: Path) -> None:
    _task(tmp_path, "# Abas e divisão do painel (C-tabs)\n\nbody")
    assert heading_title("Leia `TAREFA.md` com read_file", tmp_path) == (
        "Abas e divisão do painel (C-tabs)"
    )


def test_a_missing_file_or_one_without_a_heading_gives_nothing(tmp_path: Path) -> None:
    assert heading_title("Leia `TAREFA.md`", tmp_path) == ""
    _task(tmp_path, "no heading here\n## only a second level")
    assert heading_title("Leia `TAREFA.md`", tmp_path) == ""
    assert heading_title("Leia `TAREFA.md`", "") == ""
    assert heading_title("Leia `TAREFA.md`", tmp_path / "gone") == ""


def test_a_file_outside_the_workspace_is_never_read(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = _task(tmp_path, "# Secret outside")
    assert heading_title("Leia `../TAREFA.md`", ws) == ""
    assert heading_title(f"Leia `{outside}`", ws) == ""
    # An absolute path INSIDE the workspace is the same file as its relative name.
    inside = _task(ws, "# Inside")
    assert heading_title(f"Leia `{inside}`", ws) == "Inside"


def test_a_heading_past_the_read_bound_is_not_found(tmp_path: Path) -> None:
    _task(tmp_path, "x\n" * HEADING_READ_BYTES + "# Too far\n")
    assert heading_title("Leia `TAREFA.md`", tmp_path) == ""


def test_the_heading_is_cleaned_like_any_title(tmp_path: Path) -> None:
    _task(tmp_path, "# Fix ‮evil\x1b[31m " + "y" * 300 + "\n")
    title = heading_title("Leia `TAREFA.md`", tmp_path)
    assert "‮" not in title and "\x1b" not in title and len(title) == TITLE_MAX


def test_a_link_is_never_followed_even_to_a_file_inside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _task(tmp_path, "# Real task", name="real.md")
    _link(tmp_path / "TAREFA.md", tmp_path / "real.md")
    assert heading_title("Leia `TAREFA.md`", tmp_path) == ""
    # A linked FOLDER inside the workspace, pointing inside it too: nothing but the link check
    # stands between the reference and the file (the file itself is regular and resolves inside).
    _task(tmp_path / "real", "# Through a linked folder")
    _link(tmp_path / "docs", tmp_path / "real")
    assert heading_title("Leia `docs/TAREFA.md`", tmp_path) == ""
    # Sabotage: with the link check off, the same call reads through the link. So the "" above is
    # the guard's doing, not an accident of the fixture.
    monkeypatch.setattr(conversation_title, "_is_link", lambda _path: False)
    assert heading_title("Leia `docs/TAREFA.md`", tmp_path) == "Through a linked folder"
    # The file link is still refused without it: the last component must be a regular file itself.
    assert heading_title("Leia `TAREFA.md`", tmp_path) == ""


def test_a_linked_folder_pointing_out_is_refused_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    out = tmp_path / "out"
    _task(out, "# Outside via a link")
    _link(ws / "docs", out)
    assert heading_title("Leia `docs/TAREFA.md`", ws) == ""
    # With the link check off the containment check still holds: the resolved file is outside.
    monkeypatch.setattr(conversation_title, "_is_link", lambda _path: False)
    assert heading_title("Leia `docs/TAREFA.md`", ws) == ""


# ---------------------------------------------------------------------------- the store's list


def _store(tmp_path: Path) -> CodeSessionStore:
    return CodeSessionStore(tmp_path / "code_sessions")


def _save(store: CodeSessionStore, sid: str, first: str) -> None:
    store.save(
        CodeSession(
            cast(Any, _Agent()),
            session_id=sid,
            workspace="/w",
            messages=[{"role": "user", "content": first}],
        )
    )


def _row(store: CodeSessionStore, sid: str) -> dict[str, Any]:
    return next(r for r in store.list_meta() if r["id"] == sid)


def test_each_source_outranks_the_one_below_it_in_the_list(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _save(store, "s", "Leia `TAREFA.md` e execute")
    assert (_row(store, "s")["title"], _row(store, "s")["title_source"]) == (
        "Leia `TAREFA.md` e execute",
        "message",
    )
    store.marks.stamp_title("s", "Heading", source="heading")
    assert _row(store, "s")["title_source"] == "heading"
    store.marks.stamp_title("s", "Abas (C-tabs)", source="caller")
    assert (_row(store, "s")["title"], _row(store, "s")["title_source"]) == (
        "Abas (C-tabs)",
        "caller",
    )
    store.marks.rename("s", "  My   name ")
    assert (_row(store, "s")["title"], _row(store, "s")["title_source"]) == ("My name", "owner")
    store.marks.rename("s", "   ")  # blank gives the automatic name back
    assert (_row(store, "s")["title"], _row(store, "s")["title_source"]) == (
        "Abas (C-tabs)",
        "caller",
    )


def test_a_title_survives_compaction_and_saves(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _save(store, "s", "Leia `TAREFA.md`")
    store.marks.stamp_title("s", "Abas (C-tabs)", source="caller")
    store.marks.rename("s", "Owner's")
    # Compaction drops the first message; the next save writes the transcript from the session.
    store.save(
        CodeSession(
            cast(Any, _Agent()),
            session_id="s",
            workspace="/w",
            messages=[
                {"role": "user", "content": "[earlier conversation, compacted] 129 earlier messages"},
                {"role": "user", "content": "a later ask"},
            ],
        )
    )
    assert _row(store, "s")["title"] == "Owner's"
    store.marks.rename("s", "")
    assert _row(store, "s")["title"] == "Abas (C-tabs)"


def test_a_fork_keeps_the_titles_and_nothing_else_of_the_marks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _save(store, "s", "Leia `TAREFA.md`")
    store.marks.stamp_title("s", "Abas (C-tabs)", source="caller")
    store.marks.rename("s", "Owner's")
    store.marks.archive("s", at=1.0)
    new = store.fork("s")
    assert new is not None
    row = _row(store, new)
    assert (row["title"], row["title_source"]) == ("Owner's", "owner")
    copied = store.marks.get(new)
    assert copied.stamped_title == "Abas (C-tabs)" and copied.archived_at is None


def test_a_title_from_another_version_is_read_with_suspicion(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _save(store, "s", "first ask")
    store.marks.path.write_text(
        json.dumps(
            {
                "sessions": {
                    "s": {
                        "title": 42,
                        "stamped_title": "‮" + "z" * 400,
                        "stamped_title_source": "model",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    row = _row(store, "s")
    assert (row["title"], row["title_source"]) == ("first ask", "message")


# ---------------------------------------------------------------------------- through the app


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> App:
    Agent.hold.set()
    return App(tmp_path, monkeypatch, Agent)


def _turn(app: App, message: str, **extra: Any) -> str:
    body: dict[str, Any] = {
        "message": message,
        "workspace": str(app.folder),
        "stream": False,
        **extra,
    }
    text = app.client.post("/api/code/turn", json=body).text
    found = re.search(r'"session_id": ?"([0-9a-f]+)"', text)
    assert found, text[:400]
    return found.group(1)


def test_the_first_turn_stamps_the_task_files_heading(app: App) -> None:
    _task(app.folder, "# Abas e divisão do painel (C-tabs)\n")
    sid = _turn(app, "Leia `TAREFA.md` com read_file e execute exatamente o que ele pede")
    row = app.rows()[sid]
    assert (row["title"], row["title_source"]) == ("Abas e divisão do painel (C-tabs)", "heading")
    # A later turn naming another file does not retitle it: the heading is the first turn's.
    _task(app.folder, "# Something else\n", name="OUTRA.md")
    _turn(app, "Agora leia `OUTRA.md`", session_id=sid)
    assert app.rows()[sid]["title"] == "Abas e divisão do painel (C-tabs)"


def test_a_callers_title_reaches_the_conversation_and_beats_the_heading(app: App) -> None:
    _task(app.folder, "# Heading title\n")
    sid = _turn(app, "Leia `TAREFA.md`", title="  Abas (C-tabs)\n")
    assert (app.rows()[sid]["title"], app.rows()[sid]["title_source"]) == (
        "Abas (C-tabs)",
        "caller",
    )
    other = _turn(app, "Leia `TAREFA.md`")
    _turn(app, "continue", session_id=other, title="Named later")
    assert app.rows()[other]["title"] == "Named later"


def test_the_owner_renames_and_an_empty_title_restores_the_automatic_one(app: App) -> None:
    sid = _turn(app, "Leia `TAREFA.md`", title="Caller's")
    named = app.client.patch(f"/api/code/sessions/{sid}", json={"title": "Mine"})
    assert named.status_code == 200
    assert named.json() == {"id": sid, "title": "Mine", "title_source": "owner"}
    # A turn that brings a caller title afterwards does not take the owner's name away.
    _turn(app, "continue", session_id=sid, title="Caller again")
    assert app.rows()[sid]["title"] == "Mine"
    back = app.client.patch(f"/api/code/sessions/{sid}", json={"title": ""})
    assert back.json() == {"id": sid, "title": "Caller again", "title_source": "caller"}


def test_renaming_an_unknown_conversation_is_a_404(app: App) -> None:
    assert app.client.patch("/api/code/sessions/nope", json={"title": "x"}).status_code == 404
    assert app.client.patch("/api/code/sessions/!!!", json={"title": "x"}).status_code == 400
    assert app.client.patch("/api/code/sessions/nope", json={"title": "x" * 501}).status_code == 422


def test_a_fork_through_the_app_carries_the_name(app: App) -> None:
    sid = _turn(app, "Leia `TAREFA.md`", title="Abas (C-tabs)")
    fork = app.client.post(f"/api/code/sessions/{sid}/fork").json()
    assert (fork["title"], fork["title_source"]) == ("Abas (C-tabs)", "caller")


# ---------------------------------------------------------------------------- the bridge


def test_rename_is_an_operate_route_to_the_owners_patch() -> None:
    route = ROUTES["conversations.rename"]
    assert (route.method, route.path, route.tier) == (
        "PATCH",
        "/api/code/sessions/{session_id}",
        "operate",
    )
    assert ROUTES["conversations.archive"].tier == route.tier


def test_desktop_send_passes_the_title_through() -> None:
    calls: list[Any] = []

    def http(method: str, url: str, token: str, body: Any, timeout: float) -> tuple[int, Any]:
        calls.append(body)
        return 200, {"status": 200, "job": {"done": True}}

    found = {"url": "http://127.0.0.1:1", "token": "t", "full": False}
    mcp = DesktopMCP(discover=lambda: found, http=http)
    send = next(s for s in mcp.tool_specs() if s["name"] == "desktop_send")
    assert "title" in send["inputSchema"]["properties"]
    mcp.dispatch("desktop_send", {"message": "go", "title": "Abas (C-tabs)"})
    assert calls[0]["route"] == "conversations.send"
    assert calls[0]["body"] == {"message": "go", "title": "Abas (C-tabs)"}


def test_the_bridge_renames_a_conversation_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.desktop_bridge import read_discovery

    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "false")
    get_settings.cache_clear()
    store = CodeSessionStore(home / "code_sessions")
    _save(store, "abc123", "Leia `TAREFA.md`")
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach("http://127.0.0.1:65011")

    def through(client: TestClient) -> Any:
        def http(method: str, url: str, token: str, body: Any, timeout: float) -> tuple[int, Any]:
            path = "/" + url.split("/", 3)[3]
            response = client.request(
                method, path, json=body, headers={"Authorization": f"Bearer {token}"}
            )
            return response.status_code, response.json()

        return http

    try:
        with TestClient(app) as client:
            mcp = DesktopMCP(discover=read_discovery, http=through(client))
            renamed = mcp.dispatch(
                "desktop_conversations",
                {"action": "rename", "params": {"session_id": "abc123"}, "body": {"title": "Named"}},
            )
            listed = mcp.dispatch("desktop_conversations", {"action": "list"})
    finally:
        get_settings.cache_clear()
    assert '"title_source": "owner"' in renamed
    assert '"title": "Named"' in listed
    assert store.marks.get("abc123").title == "Named"
