"""``chimera code`` through the owner's background service (S2) — the second of its two transports.

``chimera code`` used to have one door: the desktop app's bridge (``/api/bridge/*``), open only
while the app's window is up with "Allow Claude to operate this app" on, carrying Claude's token —
short-lived and limited to a table of routes. Since S1 there is a second process that can run a Code
turn: ``chimera service``, the same backend without a window, which publishes its URL and the
OWNER's token in its record (:mod:`chimera.core.service_record`). This module is the terminal's
client of that record.

Preferred when a service is alive, because it is the owner's own door: the terminal talks to
``/api/code/turn`` exactly as the Code screen does (the turn is streamed as SSE and read with httpx),
so a conversation started here is a Code conversation the app lists, and one the app started can be
continued here. The bridge remains the fallback when no service runs and the app is open.

What the two doors may do, and why they differ:

* **The bridge** speaks for Claude. It may only call the routes its table lists, at the tier the
  owner set; it cannot start a conversation the owner did not open, and it answers an approval
  through the screen's own route only under "Full control".
* **The service** speaks for the owner and reaches the whole API, as the screen does — except for
  the one thing a long-lived token must not be: an approval key. An approval typed here carries the
  per-question code the asker drew (study 30, S30-30) and is written beside the question for the
  asker to check, exactly as ``chimera approve <id> --yes --code`` does; this module never sends a
  code-less yes to ``POST /api/approvals``. A refusal needs no code and goes through the route.

The record is trusted only after the server confirms it (``GET /api/info``: pid, version, id and
data folder, via :func:`~chimera.core.service_record.probe`), and only when its URL is on this
machine: a record pointing anywhere else is refused before the token leaves the process. The token
is sent in a header, through a client that ignores ``HTTP_PROXY`` (a proxy would see it), and is
masked out of every message this module raises.

Nothing on the MCP side imports this module: the bridge's clients must never fall back to the
owner's token (``tests/test_the_mcp_bridge_never_reads_the_service_token.py``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from chimera.core.service_record import Probe, ServiceRecord, probe, read_record

#: Only these hosts. The record is a file anyone on the machine could have written; a URL that
#: leaves the machine would hand the owner's token to whoever it names.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: A turn's stream sends a keep-alive every few seconds, so a read that waits this long means the
#: service is gone, not that the agent is thinking.
READ_SECONDS = 300.0
CONNECT_SECONDS = 10.0
#: Six digits, as the asker draws them (`pending.CODE_DIGITS`). Anything else is not sent: a wrong
#: code REFUSES the question on the asker's side, so a typo should not get that far.
_CODE = re.compile(r"\d{6}")

START_HINT = "chimera service start"


class ServiceUnavailable(RuntimeError):
    """The service cannot be reached or refused the request; the message says which and the fix."""


def is_loopback(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in LOOPBACK_HOSTS


@dataclass(frozen=True)
class Found:
    """What looking for the service found: a client, or why a record on disk was not used."""

    service: ServiceCode | None
    refused: str = ""


def find(
    read: Callable[[], ServiceRecord | None] = read_record,
    check: Callable[[ServiceRecord], Probe] = probe,
    transport: httpx.BaseTransport | None = None,
) -> Found:
    """The running service, confirmed by the server itself, or nothing (with the reason)."""
    record = read()
    if record is None:
        return Found(None)
    if not is_loopback(record.url):
        return Found(None, "the service record points off this machine; it was not used")
    answer = check(record)
    if answer.state == "stale":
        return Found(None)  # the pid is gone: a file left behind, nothing to report
    if not answer.trusted:
        return Found(None, f"the service record was not used: {answer.reason}")
    return Found(ServiceCode(record, transport=transport))


def iter_sse(lines: Iterable[str]) -> Iterator[tuple[str, Any]]:
    """``(event, data)`` from a ``text/event-stream``; data parsed as JSON where it is JSON."""
    event = "message"
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                text = "\n".join(data)
                try:
                    yield event, json.loads(text)
                except ValueError:
                    yield event, text
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue  # a keep-alive comment
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        text = "\n".join(data)
        try:
            yield event, json.loads(text)
        except ValueError:
            yield event, text


@dataclass
class ServiceCode:
    """The service, as ``chimera code`` needs it: list, send-and-stream, answer.

    ``transport`` is the seam the tests use (``httpx.MockTransport``); in use it is the network.
    """

    record: ServiceRecord
    transport: httpx.BaseTransport | None = None

    @property
    def home(self) -> Path:
        """The service's data folder — confirmed by ``/api/info`` before this object existed."""
        return Path(self.record.home)

    def clean(self, value: str) -> str:
        token = self.record.token
        return value.replace(token, "[redacted]") if token else value

    def _client(self, read: float = 30.0) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "base_url": self.record.url.rstrip("/"),
            "headers": {"Authorization": f"Bearer {self.record.token}"},
            "timeout": httpx.Timeout(CONNECT_SECONDS, read=read),
            # No proxy from the environment: it would carry the owner's token off a loopback call.
            "trust_env": False,
        }
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.Client(**kwargs)

    def _explain(self, status: int, body: Any) -> ServiceUnavailable:
        detail = body.get("detail", body) if isinstance(body, dict) else body
        if status == 401:
            return ServiceUnavailable(
                "The service refused the token its own record names — it restarted, or the token "
                "was changed. Run the command again."
            )
        return ServiceUnavailable(self.clean(f"The service answered {status}: {detail}"))

    def _json(self, method: str, path: str, body: Any = None) -> Any:
        try:
            with self._client() as client:
                response = client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise ServiceUnavailable(
                self.clean(f"The service stopped answering at {self.record.url} ({type(exc).__name__}).")
            ) from None
        try:
            data = response.json()
        except ValueError:
            data = response.text
        if response.status_code != 200:
            raise self._explain(response.status_code, data)
        return data

    def sessions(self) -> list[dict[str, Any]]:
        rows = self._json("GET", "/api/code/sessions")
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def stream_turn(self, body: dict[str, Any]) -> Iterator[tuple[str, Any]]:
        """One Code turn, as the screen sends it, read frame by frame until the stream ends."""
        try:
            with (
                self._client(read=READ_SECONDS) as client,
                client.stream(
                    "POST", "/api/code/turn", json=body, headers={"Accept": "text/event-stream"}
                ) as response,
            ):
                if response.status_code != 200:
                    response.read()
                    try:
                        data: Any = response.json()
                    except ValueError:
                        data = response.text
                    raise self._explain(response.status_code, data)
                yield from iter_sse(response.iter_lines())
        except httpx.HTTPError as exc:
            raise ServiceUnavailable(
                self.clean(f"The service stopped answering mid-turn ({type(exc).__name__}).")
            ) from None

    # ---- answers --------------------------------------------------------------------------------

    def code_shown(self, request_id: str) -> str:
        """Where the asker showed this question's code (`pending.code_shown`), advisory only."""
        from chimera.governance.pending import code_shown

        return code_shown(self.home, request_id)

    def refuse(self, request_id: str) -> bool:
        """A no needs no code: the screen's own route, which any answering surface may use."""
        data = self._json("POST", f"/api/approvals/{quote(request_id, safe='')}", {"approved": False})
        return bool(isinstance(data, dict) and data.get("ok"))

    def approve(self, request_id: str, code: str) -> bool:
        """A yes, carried by the question's code, for the asker to check.

        Never ``POST /api/approvals`` with ``approved: true``: in the service's own process that
        route approves a question its turn asked from memory, with no code, and the token alone
        would be an approval key. The answer is written beside the question WITH the code typed
        here — the same file ``chimera approve --yes --code`` writes — and the asker compares it to
        the one it drew. A wrong code refuses the question there (recorded ``unverified``).
        """
        if not _CODE.fullmatch((code or "").strip()):
            return False
        from chimera.governance.pending import answer

        return answer(self.home, request_id, True, via="terminal", code=code.strip())

    def answer_question(self, request_id: str, text: str) -> bool:
        """A typed answer to the agent's own question: data for the turn, never consent."""
        path = f"/api/questions/{quote(request_id, safe='')}"
        data = self._json("POST", path, {"answer": text})
        return bool(isinstance(data, dict) and data.get("ok"))

    # ---- pairing a device (S3, ``chimera pair``) -------------------------------------------------

    def devices(self) -> dict[str, Any]:
        """The device door's state and the paired devices — never a cookie."""
        data = self._json("GET", "/api/security/devices")
        return data if isinstance(data, dict) else {}

    def open_device_door(self) -> dict[str, Any]:
        """Listen on the LAN for paired devices: the owner asked, with ``--lan``."""
        data = self._json("POST", "/api/security/devices/door", {})
        return data if isinstance(data, dict) else {}

    def pairing_code(self) -> dict[str, Any]:
        """A one-time code (five minutes, once), its pairing URLs and their QR rows."""
        data = self._json("POST", "/api/security/devices/pair")
        return data if isinstance(data, dict) else {}
