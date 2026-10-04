"""The Discord bot attaches what its turn wrote — off by default, and never from an open bot (P6.3).

Study 29, P6.3. "Send me the report as a PDF" used to produce a PDF on the VPS's disk and a sentence
in the channel saying it existed. Now the bot can attach it — and that sends the owner's data to a
channel, so every test here is about the edges: what is attached (only this turn's deliverables,
inside the workspace, of an allowed type, under the size limit), and when (the switch on AND an
allowlist set; refused at every layer otherwise). The adapter tests are the ones the plan named:
a file over the limit and a forbidden type, offered by the reply, are not sent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from chimera.api.bridge_routes import OWNER_ONLY_SETTINGS
from chimera.api.config_api import ALLOWED_KEYS, APPLIES_WHEN, NEXT_LAUNCH
from chimera.config import Settings
from chimera.server import DiscordAdapter
from chimera.server import attachments as att
from chimera.server.gateway import InboundMessage, MessageGateway, Reply
from chimera.server.manager import MessagingManager


@dataclass
class _Act:
    """The shape of `chimera.core.agent.ToolActivity`."""

    name: str
    arguments: dict[str, Any]
    ok: bool = True
    observation: str = ""


def _write(ws: Path, rel: str, size: int = 100) -> Path:
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key in ("CHIMERA_DISCORD_ATTACH_FILES", "CHIMERA_DISCORD_ALLOWED_USERS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CHIMERA_DISCORD_BOT_TOKEN", "discord-token")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


# --- what a turn wrote, and what of it may leave -----------------------------------------------------


def test_the_files_come_from_the_turns_own_writers_and_not_from_failed_calls() -> None:
    acts = [
        _Act("create_document", {"path": "report.pdf"}),
        _Act("write_file", {"path": "data.csv"}),
        _Act("render_chart", {"format": "png"}),  # the tool's own default name
        _Act("generate_image", {}),
        _Act("create_document", {"path": "failed.docx"}, ok=False),
        _Act("read_file", {"path": "secret.pdf"}),  # a reader names a file it did not write
    ]
    assert att.written_paths(acts) == ["report.pdf", "data.csv", "chart.png", "generated_image.png"]


def test_only_deliverables_inside_the_workspace_under_the_limit_are_attached(ws: Path) -> None:
    _write(ws, "out/report.pdf")
    _write(ws, "page.html")
    _write(ws, "huge.png", att.MAX_ATTACHMENT_BYTES + 1)
    _write(ws, ".chimera/ledger.csv")
    acts = [
        _Act("create_document", {"path": "out/report.pdf"}),
        _Act("write_file", {"path": "page.html"}),
        _Act("generate_image", {"out": "huge.png"}),
        _Act("write_file", {"path": ".chimera/ledger.csv"}),
        _Act("write_file", {"path": "../outside.pdf"}),
        _Act("create_document", {"path": "never-written.docx"}),
    ]
    got = att.turn_attachments(acts, ws)
    assert got.files == ((ws / "out" / "report.pdf").resolve(),)
    reasons = " | ".join(got.skipped)
    assert "page.html: type .html is not attached" in reasons
    assert "huge.png:" in reasons and "over the" in reasons
    assert "ledger.csv: a protected folder" in reasons
    assert "outside.pdf: outside the workspace" in reasons
    assert "never-written.docx: not found" in reasons


def test_code_a_turn_wrote_is_neither_attached_nor_listed_as_left_behind(ws: Path) -> None:
    # Every turn that wrote code ended its reply with a "not attached: app.py: type .py is not
    # attached" line per file. Code is the work, not a deliverable; only a deliverable left behind
    # (refused for its type, size or place) is worth a line.
    for rel in ("app.py", "config.json", "Makefile", "page.html"):
        _write(ws, rel)
    acts = [_Act("write_file", {"path": rel}) for rel in ("app.py", "config.json", "Makefile", "page.html")]
    got = att.turn_attachments(acts, ws)
    assert got.files == ()
    assert got.skipped == ("page.html: type .html is not attached",)


def test_a_markdown_or_text_report_the_turn_wrote_is_attached(ws: Path) -> None:
    # "Write a report" most often ends in a .md, and the reply carries the turn's answer, not the
    # file's contents — so the report never reached the channel.
    _write(ws, "relatorio.md")
    _write(ws, "notas.txt")
    acts = [_Act("write_file", {"path": "relatorio.md"}), _Act("write_file", {"path": "notas.txt"})]
    got = att.turn_attachments(acts, ws)
    assert got.files == ((ws / "relatorio.md").resolve(), (ws / "notas.txt").resolve())
    assert got.skipped == ()


def test_a_reply_carries_at_most_four_files(ws: Path) -> None:
    acts = [_Act("generate_image", {"out": f"img{i}.png"}) for i in range(6)]
    for i in range(6):
        _write(ws, f"img{i}.png")
    got = att.turn_attachments(acts, ws)
    assert len(got.files) == att.MAX_ATTACHMENTS
    assert sum("over 4 files" in s for s in got.skipped) == 2


# --- when: off by default, and never from an open bot ----------------------------------------------


def test_it_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, CHIMERA_DISCORD_ALLOWED_USERS="42")
    assert settings.discord_attach_files is False
    assert not att.attach_enabled(settings, "discord") and att.attach_refusal(settings, "discord") is None


def test_switched_on_with_no_allowlist_it_is_refused_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, CHIMERA_DISCORD_ATTACH_FILES="true")
    assert not att.attach_enabled(settings, "discord")
    refusal = att.attach_refusal(settings, "discord")
    assert refusal and "OPEN" in refusal and "CHIMERA_DISCORD_ALLOWED_USERS" in refusal


def test_switched_on_with_an_allowlist_it_attaches_and_only_on_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch, CHIMERA_DISCORD_ATTACH_FILES="true", CHIMERA_DISCORD_ALLOWED_USERS="42"
    )
    assert att.attach_enabled(settings, "discord")
    assert not att.attach_enabled(settings, "telegram")


def test_the_adapter_refuses_the_switch_itself_when_its_bot_is_open(ws: Path) -> None:
    """Whoever builds it, and whatever they pass: an open bot never attaches."""
    assert DiscordAdapter("t", attach_files=True, workspace=ws).attach_files is False
    assert DiscordAdapter("t", allowed_users={"42"}, attach_files=True, workspace=ws).attach_files
    assert DiscordAdapter("t", allowed_users={"42"}, attach_files=True).attach_files is False


def test_the_switch_is_on_the_screen_owner_only_and_applies_at_the_next_launch() -> None:
    assert "CHIMERA_DISCORD_ATTACH_FILES" in ALLOWED_KEYS
    assert "CHIMERA_DISCORD_ATTACH_FILES" in OWNER_ONLY_SETTINGS
    assert APPLIES_WHEN["CHIMERA_DISCORD_ATTACH_FILES"] == NEXT_LAUNCH


# --- the adapter: the last check before bytes leave --------------------------------------------------


class _Typing:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


def _respond(adapter: DiscordAdapter, reply: str) -> tuple[list[str], list[list[Path]]]:
    texts: list[str] = []
    batches: list[list[Path]] = []

    async def send(text: str) -> None:
        texts.append(text)

    async def send_files(paths: list[Path]) -> None:
        batches.append(paths)

    inbound = InboundMessage(text="hi", chat_id="9", platform="discord", user="42")
    asyncio.run(
        adapter._respond(inbound, lambda _m: reply, typing=_Typing, send=send, send_files=send_files)
    )
    return texts, batches


def test_a_file_over_the_limit_or_of_a_forbidden_type_is_not_sent_even_if_the_reply_names_it(
    ws: Path,
) -> None:
    ok = _write(ws, "report.pdf")
    big = _write(ws, "big.pdf", att.MAX_ATTACHMENT_BYTES + 1)
    exe = _write(ws, "tool.exe")
    page = _write(ws, "page.html")
    adapter = DiscordAdapter("t", allowed_users={"42"}, attach_files=True, workspace=ws)

    texts, batches = _respond(adapter, Reply("here it is", [ok, big, exe, page]))

    assert texts == ["here it is"]
    assert batches == [[ok]]


def test_a_file_that_grew_past_the_limit_after_the_turn_is_dropped_at_send_time(ws: Path) -> None:
    path = _write(ws, "report.pdf")
    adapter = DiscordAdapter("t", allowed_users={"42"}, attach_files=True, workspace=ws)
    reply = Reply("done", [path])
    path.write_bytes(b"x" * (att.MAX_ATTACHMENT_BYTES + 1))
    assert _respond(adapter, reply)[1] == []


def test_an_adapter_that_does_not_attach_sends_the_text_and_no_file(ws: Path) -> None:
    path = _write(ws, "report.pdf")
    off = DiscordAdapter("t", allowed_users={"42"}, workspace=ws)
    assert _respond(off, Reply("done", [path])) == (["done"], [])


def test_a_failed_upload_is_said_in_the_channel_after_the_text(ws: Path) -> None:
    path = _write(ws, "report.pdf")
    adapter = DiscordAdapter("t", allowed_users={"42"}, attach_files=True, workspace=ws)
    texts: list[str] = []

    async def send(text: str) -> None:
        texts.append(text)

    async def send_files(_paths: list[Path]) -> None:
        raise RuntimeError("413 Payload Too Large")

    inbound = InboundMessage(text="hi", chat_id="9", platform="discord", user="42")
    asyncio.run(
        adapter._respond(
            inbound, lambda _m: Reply("done", [path]), typing=_Typing, send=send, send_files=send_files
        )
    )
    assert texts[0] == "done" and "could not be attached" in texts[1]


# --- the gateway: the turn's tool calls become the reply's files ------------------------------------


class _Session:
    """Writes a PDF and an HTML page through the tool callback, as a real turn reports them."""

    max_turns = 10

    def __init__(self, ws: Path) -> None:
        self.ws = ws
        self.saw_on_tool = False

    def send(self, message: str) -> str:
        return "plain"

    def send_verbose(
        self, message: str, *, on_notice: Any = None, on_tool: Any = None, channel_note: str = ""
    ) -> Any:
        from chimera.interface.session import TurnReport

        self.saw_on_tool = on_tool is not None
        for rel in ("report.pdf", "page.html"):
            _write(self.ws, rel)
            if on_tool is not None:
                on_tool(_Act("write_file", {"path": rel}))
        return TurnReport(answer="here is the report", stopped_reason="final")


def test_the_gateway_returns_the_turns_files_and_says_which_it_left_behind(ws: Path) -> None:
    from functools import partial

    session = _Session(ws)
    gateway = MessageGateway(
        lambda: session, warnings_in_reply=True,  # type: ignore[arg-type,return-value]
        attach=partial(att.turn_attachments, workspace=ws),
    )
    reply = gateway.on_message(InboundMessage(text="pdf please", chat_id="c", platform="discord"))

    assert isinstance(reply, Reply)
    assert reply.files == ((ws / "report.pdf").resolve(),)
    assert reply.startswith("here is the report")
    assert "not attached: page.html: type .html is not attached" in reply


def test_without_the_hook_the_gateway_does_not_even_collect_the_tool_calls(ws: Path) -> None:
    session = _Session(ws)
    gateway = MessageGateway(lambda: session, warnings_in_reply=True)  # type: ignore[arg-type,return-value]
    reply = gateway.on_message(InboundMessage(text="pdf please", chat_id="c", platform="discord"))
    assert not isinstance(reply, Reply) and not session.saw_on_tool


# --- the two construction paths ----------------------------------------------------------------------


def _manager(settings: Settings, ws: Path) -> MessagingManager:
    return MessagingManager(
        settings=settings, backend=object(), model=None, max_steps=1, workspace=ws  # type: ignore[arg-type]
    )


def test_the_apps_bot_attaches_only_when_switched_on_and_closed(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    open_on = _manager(_settings(monkeypatch, CHIMERA_DISCORD_ATTACH_FILES="true"), ws)
    adapter = open_on._default_adapter("discord")
    assert adapter.attach_files is False
    assert open_on._gateway_on_message(adapter).__self__._attach is None  # type: ignore[attr-defined]

    closed_on = _manager(
        _settings(monkeypatch, CHIMERA_DISCORD_ATTACH_FILES="true", CHIMERA_DISCORD_ALLOWED_USERS="42"), ws
    )
    adapter = closed_on._default_adapter("discord")
    assert adapter.attach_files is True and adapter.workspace == ws
    assert closed_on._gateway_on_message(adapter).__self__._attach is not None  # type: ignore[attr-defined]


def test_chimera_serve_warns_when_the_switch_is_on_and_the_bot_is_open(
    monkeypatch: pytest.MonkeyPatch, ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from chimera.cli import main as cli

    settings = _settings(monkeypatch, CHIMERA_DISCORD_ATTACH_FILES="true")
    adapter = cli._messaging_adapter(settings, "discord")
    assert "files are NOT attached" in capsys.readouterr().out.replace("\n", " ")
    assert cli._turn_attachments(adapter, settings, ws) is None and adapter.attach_files is False

    closed = _settings(
        monkeypatch, CHIMERA_DISCORD_ATTACH_FILES="true", CHIMERA_DISCORD_ALLOWED_USERS="42"
    )
    adapter = cli._messaging_adapter(closed, "discord")
    assert cli._turn_attachments(adapter, closed, ws) is not None
    assert adapter.attach_files is True and adapter.workspace == ws


def test_chimera_serve_leaves_attachments_off_when_the_switch_is(
    monkeypatch: pytest.MonkeyPatch, ws: Path
) -> None:
    from chimera.cli import main as cli

    settings = _settings(monkeypatch, CHIMERA_DISCORD_ALLOWED_USERS="42")
    adapter = cli._messaging_adapter(settings, "discord")
    assert cli._turn_attachments(adapter, settings, ws) is None and adapter.attach_files is False
