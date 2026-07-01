"""calendar_events tool — list events from an iCalendar (.ics) feed.

Most calendars (Google, Outlook, Fastmail, ...) expose a secret .ics URL. This fetches it
and parses VEVENT blocks with the stdlib — no key, no dependency. Point it at a URL per
call, or set ``CHIMERA_CALENDAR_ICS_URL`` as the default.
"""

from __future__ import annotations

import re
from typing import Any

from chimera.config import get_settings
from chimera.tools.base import Tool

_VEVENT = re.compile(r"BEGIN:VEVENT(.*?)END:VEVENT", re.DOTALL)


def _field(block: str, key: str) -> str:
    match = re.search(rf"^{key}[^:\r\n]*:(.*)$", block, re.MULTILINE)
    return match.group(1).strip() if match else ""


def parse_ics(text: str) -> list[dict[str, str]]:
    """Parse VEVENT summary/start/location from iCalendar text (pure)."""
    events: list[dict[str, str]] = []
    for block in _VEVENT.findall(text):
        summary = _field(block, "SUMMARY")
        if not summary:
            continue
        events.append(
            {"summary": summary, "start": _field(block, "DTSTART"), "location": _field(block, "LOCATION")}
        )
    events.sort(key=lambda event: event["start"])
    return events


class CalendarEventsTool(Tool):
    name = "calendar_events"
    description = "List events from an iCalendar (.ics) URL (start time, title, location)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The .ics feed URL (falls back to config)."},
            "max_results": {"type": "integer", "description": "Max events (default 10)."},
        },
        "required": [],
    }

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy

        url = str(kwargs.get("url") or get_settings().calendar_ics_url or "").strip()
        if not url:
            return "error: calendar_events needs an .ics URL (arg 'url' or CHIMERA_CALENDAR_ICS_URL)."
        try:
            response = httpx.get(url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"error: calendar fetch failed: {exc}"
        events = parse_ics(response.text)
        if not events:
            return "no events found in the calendar"
        max_results = int(kwargs.get("max_results") or 10)
        lines = [
            f"- {event['start']} {event['summary']}"
            + (f" @ {event['location']}" if event["location"] else "")
            for event in events[:max_results]
        ]
        return f"{len(events)} event(s); showing {min(len(events), max_results)}:\n" + "\n".join(lines)
