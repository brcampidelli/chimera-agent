"""Tests for code_interpreter / calendar_events / read_email reference tools."""

from __future__ import annotations

from typing import Any

import pytest

from chimera.config import get_settings
from chimera.tools.calendar import CalendarEventsTool, parse_ics
from chimera.tools.code import CodeInterpreterTool
from chimera.tools.email import ReadEmailTool


def test_code_interpreter_persists_state() -> None:
    tool = CodeInterpreterTool()
    assert tool.run(code="x = 21") == "(no output)"
    assert "42" in tool.run(code="print(x * 2)")  # x persisted across calls


def test_code_interpreter_reset_clears_state() -> None:
    tool = CodeInterpreterTool()
    tool.run(code="y = 5")
    tool.run(code="pass", reset=True)
    assert "NameError" in tool.run(code="print(y)")


def test_code_interpreter_reports_error() -> None:
    assert "ZeroDivisionError" in CodeInterpreterTool().run(code="1 / 0")


_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Team standup
DTSTART:20260115T090000Z
LOCATION:Zoom
END:VEVENT
BEGIN:VEVENT
SUMMARY:Lunch
DTSTART:20260114T120000Z
END:VEVENT
END:VCALENDAR"""


def test_parse_ics_sorted_by_start() -> None:
    events = parse_ics(_ICS)
    assert len(events) == 2
    assert events[0]["summary"] == "Lunch"  # 14th sorts before 15th
    assert events[1]["summary"] == "Team standup" and events[1]["location"] == "Zoom"


def test_calendar_tool_without_url_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_CALENDAR_ICS_URL", raising=False)
    get_settings.cache_clear()
    assert CalendarEventsTool().run().startswith("error:")


def test_calendar_tool_fetches_and_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class Resp:
        text = _ICS

        def raise_for_status(self) -> None: ...

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    out = CalendarEventsTool().run(url="https://example.com/cal.ics")
    assert "Team standup" in out and "Lunch" in out and "Zoom" in out


def test_read_email_without_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CHIMERA_IMAP_HOST", "CHIMERA_IMAP_USER", "CHIMERA_IMAP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    assert ReadEmailTool().run().startswith("error:")


def test_read_email_lists_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    import imaplib

    monkeypatch.setenv("CHIMERA_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("CHIMERA_IMAP_USER", "u@example.com")
    monkeypatch.setenv("CHIMERA_IMAP_PASSWORD", "secret")
    get_settings.cache_clear()
    raw = b"From: alice@example.com\r\nSubject: Hello there\r\n\r\nbody"

    class FakeIMAP:
        def __init__(self, host: str, port: int) -> None: ...

        def __enter__(self) -> FakeIMAP:
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def login(self, user: str, password: str) -> None: ...

        def select(self, mailbox: str) -> None: ...

        def search(self, charset: Any, criteria: str) -> tuple[str, list[bytes]]:
            return "OK", [b"1 2"]

        def fetch(self, msg_id: bytes, spec: str) -> tuple[str, list[Any]]:
            return "OK", [(b"1 (RFC822 {40}", raw)]

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    out = ReadEmailTool().run(max_results=2)
    assert "alice@example.com" in out and "Hello there" in out
