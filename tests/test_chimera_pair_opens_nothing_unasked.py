"""`chimera pair` prints a code through the running service, and opens the LAN door only on `--lan`.

The listener a phone types the code into is off by default (S3: nothing listens off loopback unless
the owner turns it on). So the command, with the door shut, says how to open it and mints nothing;
with ``--lan`` — the owner saying so — it opens the door and then asks for the code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from chimera.cli import code_service
from chimera.cli.main import app
from chimera.core.service_record import new_record

runner = CliRunner()


def _service(tmp_path: Path, calls: list[str], *, door_open: bool) -> code_service.Found:
    state = {"open": door_open}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        assert request.headers["authorization"] == "Bearer tok-0123456789"
        if request.url.path == "/api/security/devices":
            return httpx.Response(200, json={"door": {"open": state["open"]}, "devices": []})
        if request.url.path == "/api/security/devices/door":
            state["open"] = True
            return httpx.Response(200, json={"open": True, "port": 8123, "urls": []})
        if request.url.path == "/api/security/devices/pair":
            body: dict[str, Any] = {
                "code": "ABCD-EFGH", "expires_at": 1.0, "seconds": 300,
                "urls": ["http://10.0.0.2:8123/pair?code=ABCD-EFGH"], "qr": [],
            }
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    record = new_record(url="http://127.0.0.1:45278", token="tok-0123456789", home=tmp_path)
    return code_service.Found(code_service.ServiceCode(record, transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("command", [["pair"], ["service", "pair"]])
def test_with_the_door_shut_it_says_how_and_mints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(code_service, "find", lambda: _service(tmp_path, calls, door_open=False))
    result = runner.invoke(app, command)
    assert result.exit_code == 1, result.output
    assert "--lan" in result.output
    assert calls == ["GET /api/security/devices"], "it opened something or minted a code unasked"


def test_with_lan_it_opens_the_door_then_prints_the_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(code_service, "find", lambda: _service(tmp_path, calls, door_open=False))
    result = runner.invoke(app, ["pair", "--lan", "--json"])
    assert result.exit_code == 0, result.output
    assert calls == [
        "GET /api/security/devices",
        "POST /api/security/devices/door",
        "POST /api/security/devices/pair",
    ]
    printed = json.loads(result.output.strip().splitlines()[-1])
    assert printed["code"] == "ABCD-EFGH" and printed["urls"][0].endswith("code=ABCD-EFGH")
    assert "tok-0123456789" not in result.output


def test_without_a_running_chimera_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_service, "find", lambda: code_service.Found(None))
    result = runner.invoke(app, ["pair"])
    assert result.exit_code == 1 and "chimera service start" in result.output
