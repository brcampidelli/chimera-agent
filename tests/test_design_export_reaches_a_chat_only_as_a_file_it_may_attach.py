"""Exporting a design as PDF, PNG or PPTX: the route, the tool, and the way to a linked chat (PR D7).

Pinned here:

- ``POST /api/design/{id}/export`` writes html, pdf, png and pptx, each a confirmed download with
  ``attachment``, ``nosniff`` and its own media type; a format it does not know is a 400 that lists
  the four; a machine that lacks the renderer answers 503 saying what to install, and audits nothing;
- the ``design`` tool writes every format into ``design-exports/`` under a safe name, never over a
  file, only when the posture allows a workspace write, and a missing renderer is an error with
  nothing written;
- on a linked chat the tool never produces HTML (a text summary instead), defaults to PDF, refuses a
  multi-artboard PNG (a zip no chat receives), and says the file goes back only where attachments
  are on;
- :mod:`chimera.server.attachments` takes a design export from what the tool reported — only an
  ``export`` call that worked, only ``design-exports/`` and only PDF/PNG/PPTX, never HTML;
- a linked chat's reply carries the file through the same gate, only for an adapter that attaches,
  and says so when the conversation's folder is not the bot's.

Exports that need Chromium are stubbed where the test is about the PATH a file takes, not its bytes
(those are pinned in ``test_design_exports_to_pdf_png_and_pptx``); the PPTX is real where python-pptx
is installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import chimera.tools.design as design_tool
from chimera.api.design_api import register_design_api
from chimera.config import Settings
from chimera.core.agent import ToolActivity
from chimera.design import exports
from chimera.design.exports import PPTX_MEDIA_TYPE, Exported, ExportUnavailable
from chimera.design.store import DesignStore
from chimera.server import attachments as att
from chimera.server.channel_links import ChannelLinks, LinkedTrace, linked_reply
from chimera.server.gateway import InboundMessage, Reply
from chimera.tools.base import Refusal
from chimera.tools.design import EXPORT_DIRNAME, DesignTool

OWNER = "owner-1"
FAKE_PDF = b"%PDF-1.7\n% a stand-in: the bytes are pinned in the exporter tests\n%%EOF\n"


def _page_op(page: str = "p1", board: str = "a1", width: int = 800) -> dict[str, Any]:
    return {"type": "page.add", "page": {"id": page, "name": "Home", "artboards": [
        {"id": board, "name": "Desktop", "width": width, "height": 600, "background": "#FFFFFF",
         "nodeIds": []}]}}


def _fake_export(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for the Chromium formats: records the format and returns a small file."""
    asked: list[str] = []
    real = exports.export_document

    def fake(doc: Any, fmt: str, **kwargs: Any) -> Exported:
        asked.append(fmt)
        if fmt == "pdf":
            return Exported(FAKE_PDF, "application/pdf", "pdf")
        if fmt == "png":
            return Exported(b"\x89PNG\r\n\x1a\n-stand-in", "image/png", "png")
        return real(doc, fmt, **kwargs)

    monkeypatch.setattr(exports, "export_document", fake)
    monkeypatch.setattr(design_tool, "export_document", fake)
    return asked


# ------------------------------------------------------------------ the route


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    home = tmp_path / "home"
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    app = FastAPI()
    register_design_api(app, Depends(lambda: None), settings)
    return TestClient(app), home


def _board(client: TestClient) -> str:
    doc = client.post("/api/design", json={"title": "Plano trimestral"}).json()
    applied = client.post(f"/api/design/{doc['id']}/ops",
                          json={"expected_revision": doc["revision"], "operations": [_page_op()]})
    assert applied.status_code == 200, applied.text
    return str(doc["id"])


def _audit(home: Path) -> list[dict[str, Any]]:
    path = home / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    return [json.loads(line) for line in lines if line]


@pytest.mark.parametrize(
    ("fmt", "media", "extension"),
    [("html", "text/html", "html"), ("pdf", "application/pdf", "pdf"),
     ("png", "image/png", "png"), ("pptx", PPTX_MEDIA_TYPE, "pptx")],
)
def test_every_format_is_a_confirmed_download_with_its_media_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fmt: str, media: str, extension: str
) -> None:
    if fmt == "pptx":
        pytest.importorskip("pptx", reason="python-pptx is the documents-out extra")
    _fake_export(monkeypatch)
    client, home = _client(tmp_path)
    design_id = _board(client)
    url = f"/api/design/{design_id}/export"

    refused = client.post(url, json={"format": fmt})
    assert refused.status_code == 400 and "confirm" in refused.json()["detail"]

    out = client.post(url, json={"format": fmt.upper(), "confirm": True})
    assert out.status_code == 200, out.text
    assert out.headers["content-type"].startswith(media)
    assert out.headers["content-disposition"] == f'attachment; filename="design-{design_id}.{extension}"'
    assert out.headers["x-content-type-options"] == "nosniff"
    assert out.headers["cache-control"] == "no-store"
    events = [e for e in _audit(home) if e["type"] == "design.export"]
    assert len(events) == 1 and events[0]["format"] == fmt and events[0]["bytes"] == len(out.content)
    assert "Plano" not in (home / "audit.jsonl").read_text(encoding="utf-8")


def test_an_unknown_format_lists_the_four(tmp_path: Path) -> None:
    client, home = _client(tmp_path)
    design_id = _board(client)
    out = client.post(f"/api/design/{design_id}/export", json={"format": "svg", "confirm": True})
    assert out.status_code == 400
    assert out.json()["detail"] == "format must be one of: html, pdf, png, pptx"
    assert not [e for e in _audit(home) if e["type"] == "design.export"]


def test_a_missing_renderer_is_a_503_that_says_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_doc: Any, _fmt: str, **_kw: Any) -> Exported:
        raise ExportUnavailable("run `playwright install chromium` (about 150 MB, once)")

    monkeypatch.setattr(exports, "export_document", unavailable)
    client, home = _client(tmp_path)
    design_id = _board(client)
    out = client.post(f"/api/design/{design_id}/export", json={"format": "pdf", "confirm": True})
    assert out.status_code == 503
    assert "playwright install chromium" in out.json()["detail"]
    assert not [e for e in _audit(home) if e["type"] == "design.export"]


def test_the_export_route_stays_off_the_bridge() -> None:
    from chimera.api.bridge_routes import ROUTES

    assert not [name for name, route in ROUTES.items() if route.path.endswith("/export")
                and route.path.startswith("/api/design")]


# ------------------------------------------------------------------ the tool


def _tool(tmp_path: Path, **kwargs: Any) -> tuple[DesignTool, list[dict[str, Any]]]:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    tool = DesignTool(DesignStore(tmp_path / "home" / "designs"), workspace=ws, **kwargs)
    frames: list[dict[str, Any]] = []
    tool.on_design = frames.append
    return tool, frames


def _doc_with(tool: DesignTool, *ops: dict[str, Any]) -> str:
    writer = DesignTool(tool.store, workspace=tool.workspace)
    created = json.loads(writer.run(action="create", title="Launch deck"))
    for revision, op in enumerate(ops or (_page_op(),), start=1):
        writer.run(action="apply", document_id=created["created"], expected_revision=revision,
                   operations=[op])
    return str(created["created"])


def test_the_tool_writes_a_pptx_under_a_safe_name_and_never_over_a_file(tmp_path: Path) -> None:
    pytest.importorskip("pptx", reason="python-pptx is the documents-out extra")
    tool, frames = _tool(tmp_path)
    doc_id = _doc_with(tool)
    first = json.loads(tool.run(action="export", document_id=doc_id, format="pptx",
                                filename="../../deck"))
    second = json.loads(tool.run(action="export", document_id=doc_id, format="pptx",
                                 filename="../../deck"))
    assert first["exported"] == f"{EXPORT_DIRNAME}/deck.pptx"
    assert second["exported"] == f"{EXPORT_DIRNAME}/deck-2.pptx"
    assert (tmp_path / "ws" / first["exported"]).read_bytes()[:2] == b"PK"
    assert first["bytes"] == (tmp_path / "ws" / first["exported"]).stat().st_size
    assert frames[-1]["action"] == "export_pptx" and frames[-1]["ops_applied"] == 0
    assert "attachment" not in first, "the screen is not told about chats"


def test_export_html_is_still_the_html_export(tmp_path: Path) -> None:
    tool, frames = _tool(tmp_path)
    doc_id = _doc_with(tool)
    out = json.loads(tool.run(action="export_html", document_id=doc_id))
    assert out["exported"].endswith(".html") and out["format"] == "html"
    assert frames[-1]["action"] == "export_html"


@pytest.mark.parametrize("fmt", ["pdf", "png", "pptx", "html"])
def test_a_read_only_posture_writes_no_export_of_any_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fmt: str
) -> None:
    asked = _fake_export(monkeypatch)
    tool, frames = _tool(tmp_path, allow_writes=False)
    doc_id = _doc_with(tool)
    assert isinstance(tool.run(action="export", document_id=doc_id, format=fmt), Refusal)
    assert not (tmp_path / "ws" / EXPORT_DIRNAME).exists()
    assert asked == [] and frames == [], "nothing was rendered for a refused write"


def test_a_missing_renderer_is_an_error_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_doc: Any, _fmt: str, **_kw: Any) -> Exported:
        raise ExportUnavailable("Chromium is not installed; about 150 MB, once")

    monkeypatch.setattr(design_tool, "export_document", unavailable)
    tool, frames = _tool(tmp_path)
    doc_id = _doc_with(tool)
    out = tool.run(action="export", document_id=doc_id, format="pdf")
    assert out.startswith("error:") and "150 MB" in out
    assert not list((tmp_path / "ws").rglob("*.pdf")) and frames == []


def test_an_unknown_format_is_an_error(tmp_path: Path) -> None:
    tool, _ = _tool(tmp_path)
    doc_id = _doc_with(tool)
    assert tool.run(action="export", document_id=doc_id, format="svg").startswith(
        "error: format must be one of"
    )


# ------------------------------------------------------------------ a linked chat's tool


def test_a_bot_never_gets_html_and_defaults_to_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _fake_export(monkeypatch)
    tool, _ = _tool(tmp_path, surface="bot")
    doc_id = _doc_with(tool)

    for call in ({"action": "export_html"}, {"action": "export", "format": "html"}):
        summary = json.loads(tool.run(document_id=doc_id, **call))
        assert summary["exported"] is False and "cannot receive an HTML file" in summary["note"]
    assert not list((tmp_path / "ws").rglob("*.html"))

    out = json.loads(tool.run(action="export", document_id=doc_id))
    assert asked == ["pdf"]
    assert out["exported"] == f"{EXPORT_DIRNAME}/Launch-deck.pdf"
    assert "attachments on" in out["attachment"]


def test_a_bot_is_told_to_ask_for_a_pdf_when_the_png_would_be_a_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _fake_export(monkeypatch)
    tool, _ = _tool(tmp_path, surface="bot")
    doc_id = _doc_with(tool, _page_op("p1", "a1"), _page_op("p2", "a2", 400))
    out = tool.run(action="export", document_id=doc_id, format="png")
    assert out.startswith("error:") and "pdf" in out
    assert asked == []


# ------------------------------------------------------------------ attachments


def _export_activity(path: str, *, ok: bool = True, action: str = "export") -> ToolActivity:
    return ToolActivity("design", {"action": action, "document_id": "d1"}, ok,
                        json.dumps({"exported": path, "format": Path(path).suffix[1:]}))


def test_attachments_read_a_design_export_from_what_the_tool_reported() -> None:
    acts = [
        _export_activity("design-exports/deck.pdf"),
        _export_activity("design-exports/deck.pptx"),
        _export_activity("design-exports/page.html"),  # never a chat's
        _export_activity("design-exports/failed.pdf", ok=False),
        _export_activity("design-exports/old.pdf", action="export_html"),
        ToolActivity("design", {"action": "summary"}, True,
                     json.dumps({"text": '"exported": "design-exports/planted.pdf"'})),
        _export_activity("../outside.pdf"),
        _export_activity("design-exports/../.env.pdf"),
    ]
    assert att.written_paths(acts) == ["design-exports/deck.pdf", "design-exports/deck.pptx"]


def test_a_design_pdf_is_attached_and_an_html_never_is(tmp_path: Path) -> None:
    folder = tmp_path / EXPORT_DIRNAME
    folder.mkdir()
    (folder / "deck.pdf").write_bytes(FAKE_PDF)
    (folder / "page.html").write_text("<p>x</p>", encoding="utf-8")
    found = att.turn_attachments(
        [_export_activity("design-exports/deck.pdf"), _export_activity("design-exports/page.html")],
        tmp_path,
    )
    assert found.files == ((folder / "deck.pdf").resolve(),)
    assert found.skipped == ()


def test_a_linked_turns_file_outside_the_bots_folder_is_said_not_sent(tmp_path: Path) -> None:
    conversation = tmp_path / "project"
    (conversation / EXPORT_DIRNAME).mkdir(parents=True)
    (conversation / EXPORT_DIRNAME / "deck.pdf").write_bytes(FAKE_PDF)
    bot_folder = tmp_path / "bot"
    bot_folder.mkdir()
    acts = [_export_activity("design-exports/deck.pdf")]

    same = att.linked_attachments(acts, conversation, conversation)
    assert [f.name for f in same.files] == ["deck.pdf"]
    other = att.linked_attachments(acts, conversation, bot_folder)
    assert other.files == ()
    assert other.skipped == ("deck.pdf: this conversation's folder is not the bot's workspace",)


# ------------------------------------------------------------------ the linked reply


def _settings(home: Path) -> Settings:
    return Settings(_env_file=None, CHIMERA_HOME=str(home),  # type: ignore[call-arg]
                    CHIMERA_DISCORD_ALLOWED_USERS=OWNER)


def _links(tmp_path: Path) -> ChannelLinks:
    links = ChannelLinks(tmp_path / "channel_links.json")
    links.set("discord", "room-1", "code-session-1")
    return links


def _msg(text: str = "export the deck as pdf") -> InboundMessage:
    return InboundMessage(text=text, platform="discord", chat_id="room-1", user=OWNER)


def test_a_linked_reply_carries_the_exported_pdf_where_the_bot_attaches(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / EXPORT_DIRNAME).mkdir(parents=True)

    def turn(session_id: str, text: str, platform: str, *, trace: LinkedTrace | None = None) -> str:
        assert trace is not None
        trace.workspace = ws
        (ws / EXPORT_DIRNAME / "deck.pdf").write_bytes(FAKE_PDF)
        # The frame as the Code turn publishes it: clipped string arguments, the raw observation.
        trace.tool({"name": "design", "arguments": {"action": "export", "format": "pdf"},
                    "ok": True, "observation": json.dumps({"exported": "design-exports/deck.pdf"})})
        trace.tool({"name": "design", "arguments": {"action": "export", "format": "html"},
                    "ok": True, "observation": json.dumps({"exported": False})})
        return "Here is the deck."

    from functools import partial

    reply = linked_reply(_msg(), _links(tmp_path), turn, _settings(tmp_path),
                         attach=partial(att.linked_attachments, bot_workspace=ws))
    assert isinstance(reply, Reply)
    assert reply == "Here is the deck."
    assert reply.files == ((ws / EXPORT_DIRNAME / "deck.pdf").resolve(),)


def test_without_attachments_a_linked_reply_is_text_and_the_turn_is_unchanged(tmp_path: Path) -> None:
    calls: list[tuple[Any, ...]] = []

    def turn(session_id: str, text: str, platform: str) -> str:
        calls.append((session_id, text, platform))
        return "text only"

    for attach in (None, lambda acts, ws: att.Attachments()):
        reply = linked_reply(_msg(), _links(tmp_path), turn, _settings(tmp_path), attach=attach)
        assert reply == "text only" and not isinstance(reply, Reply)
    assert calls == [("code-session-1", "export the deck as pdf", "discord")] * 2


def test_a_stranger_in_a_linked_room_still_never_reaches_the_turn(tmp_path: Path) -> None:
    def turn(*_a: Any, **_kw: Any) -> str:
        raise AssertionError("a stranger reached the owner's conversation")

    stranger = InboundMessage(text="export", platform="discord", chat_id="room-1", user="someone")
    assert linked_reply(stranger, _links(tmp_path), turn, _settings(tmp_path),
                        attach=lambda acts, ws: att.Attachments()) is None


# ------------------------------------------------------------------ through the app


class _Exporter:
    """The coding agent of a linked turn: "exports" a PDF the way the design tool reports one."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.tools = args[1] if len(args) > 1 else kwargs.get("tools")
        self.config = kwargs.get("config") or (args[2] if len(args) > 2 else None)

    def run(self, task: str, *, on_tool: Any = None, **_kw: Any) -> Any:
        from chimera.core.agent import AgentResult

        if "export" in task and on_tool is not None:
            folder = _Exporter.workspace / EXPORT_DIRNAME
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "deck.pdf").write_bytes(FAKE_PDF)
            on_tool(ToolActivity("design", {"action": "export", "format": "pdf"}, True,
                                 json.dumps({"exported": "design-exports/deck.pdf"})))
        return AgentResult(
            answer=f"answered: {task[:40]}", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
        )

    workspace: Path = Path(".")


class _AttachingAdapter:
    platform = "discord"
    attach_files = True

    def send(self, *_a: Any) -> str:
        return "ok"


class _TextAdapter(_AttachingAdapter):
    attach_files = False


@pytest.fixture
def app_with_bot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import get_settings
    from chimera.interface import ChatSession
    from chimera.server.manager import MessagingManager

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_DISCORD_ALLOWED_USERS", OWNER)
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Exporter, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    _Exporter.workspace = ws
    settings = Settings(_env_file=None, CHIMERA_HOME=str(home),  # type: ignore[call-arg]
                        CHIMERA_MEMORY_BACKEND="json", CHIMERA_DISCORD_ALLOWED_USERS=OWNER,
                        CHIMERA_DISCORD_BOT_TOKEN="t")
    manager = MessagingManager(
        settings=settings, backend=object(), model=None, max_steps=2, workspace=ws,  # type: ignore[arg-type]  # the bot's own session is never built here
        adapter_factory=lambda _p: _AttachingAdapter(),
    )
    app = build_api_app(lambda: ChatSession(_Exporter()), workspace=ws, settings=settings,
                        messaging_manager=manager)
    yield TestClient(app), manager, ws
    get_settings.cache_clear()


def _open(client: TestClient, ws: Path) -> str:
    body = {"message": "start", "workspace": str(ws), "stream": False}
    text = client.post("/api/code/turn", json=body).text
    return text.split('"session_id": "')[1].split('"')[0]


def test_a_linked_turns_design_pdf_goes_back_with_the_reply(app_with_bot: Any) -> None:
    client, manager, ws = app_with_bot
    route = manager._gateway_on_message(_AttachingAdapter())
    session_id = _open(client, ws)
    assert "linked" in route(_msg(f"/link {session_id[:8]}"))

    reply = route(_msg("export the deck as pdf"))
    assert isinstance(reply, Reply), reply
    assert reply.startswith("answered: export the deck")
    assert reply.files == ((ws / EXPORT_DIRNAME / "deck.pdf").resolve(),)


def test_a_bot_that_does_not_attach_answers_a_linked_turn_in_text(app_with_bot: Any) -> None:
    client, manager, ws = app_with_bot
    route = manager._gateway_on_message(_TextAdapter())
    session_id = _open(client, ws)
    assert "linked" in route(_msg(f"/link {session_id[:8]}"))

    reply = route(_msg("export the deck as pdf"))
    assert not isinstance(reply, Reply) and reply.startswith("answered: export the deck")
