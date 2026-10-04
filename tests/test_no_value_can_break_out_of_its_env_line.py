"""No value written to ``.env`` can break out of its line, through any writer.

Found by the adversarial review of 2026-10-04. Every writer refused ``\\r`` and ``\\n`` and nothing
else, while the file was re-read with ``str.splitlines`` — which also splits on ``\\x0b``, ``\\x0c``,
``\\x1c``-``\\x1e``, ``\\x85``, ``\\u2028`` and ``\\u2029``. A value like
``python:3.12-slim<U+2028>CHIMERA_REACH=workspace_shell`` was written as one line, read back as two
on the next save of ANY key, and rewritten as a real assignment: through the desktop bridge's
``settings.edit`` on a key it may write, and through a settings suggestion whose card showed the
value on one line. Probes in the review: both landed the smuggled line.

Held here: every character that breaks a line (by category Cc, Cf, Zl, Zp, or by ``splitlines``) is
refused by every path that writes ``.env`` — the sink itself, the owner's save, the bridge's edit, a
suggestion and its approval, the key pools, a connector's key and ``chimera init``'s writer — and
the file is split on ``\\n`` only, so a separator that is already inside a line stays inside it.
"""

from __future__ import annotations

import os
import sys
import unicodedata
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.config_api import check_updates, pool_add
from chimera.api.key_vault import env_lines, set_env_entry, write_env_value
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

URL = "http://127.0.0.1:65011"

#: Every code point that may never sit inside a `.env` value: the four categories, plus whatever
#: `splitlines` splits on whatever its category.
BREAKERS: list[str] = sorted(
    {
        chr(cp)
        for cp in range(sys.maxunicode + 1)
        if not 0xD800 <= cp <= 0xDFFF
        and (
            unicodedata.category(chr(cp)) in {"Cc", "Cf", "Zl", "Zp"}
            or len(f"x{chr(cp)}y".splitlines()) > 1
        )
    }
)
#: The ones `splitlines` actually splits on — the ones that injected a line. Driven through the HTTP
#: paths, where each case is a full request.
SPLITTERS: list[str] = [ch for ch in BREAKERS if len(f"x{ch}y".splitlines()) > 1]
SMUGGLED = "CHIMERA_REACH=workspace_shell"


def test_the_probe_sets_are_what_they_claim() -> None:
    assert {"\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "} <= set(
        SPLITTERS
    )
    assert "\t" in BREAKERS and "​" in BREAKERS and "﻿" in BREAKERS
    assert "a" not in BREAKERS and " " not in BREAKERS and "é" not in BREAKERS


@pytest.mark.parametrize("writer", ["sink", "check_updates", "pool", "cli"])
def test_every_breaking_character_is_refused_by_every_function_that_writes_env(
    writer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import _set_env_var

    env = tmp_path / ".env"
    env.write_text("CHIMERA_REACH=read_only\n", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", "")
    get_settings.cache_clear()
    for ch in BREAKERS:
        value = f"openrouter/a/b{ch}{SMUGGLED}"
        with pytest.raises(ValueError):
            if writer == "sink":
                write_env_value(env, "CHIMERA_SANDBOX_IMAGE", value)
            elif writer == "check_updates":
                check_updates({"CHIMERA_SANDBOX_IMAGE": value})
            elif writer == "pool":
                pool_add("openrouter", f"sk-or-v1-abc{ch}def", env_path=env)
            else:
                _set_env_var(env, "CHIMERA_DEFAULT_MODEL", value)
    assert env.read_text(encoding="utf-8") == "CHIMERA_REACH=read_only\n"
    get_settings.cache_clear()


def test_a_connectors_key_is_held_to_the_same_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.connectors_api import set_key
    from chimera.integrations import openapi_store as store
    from tests.test_an_openapi_connector_is_get_only_untrusted_and_the_owners import _home

    home = _home(tmp_path)
    env = tmp_path / ".env"
    monkeypatch.setenv(store.derived_key_env("pets"), "")
    for ch in SPLITTERS + ["\x00", "​"]:
        with pytest.raises(store.ConnectorError):
            set_key(home, "pets", f"key{ch}{SMUGGLED}", env)
    assert not env.exists()


def test_a_separator_already_inside_a_line_stays_inside_it(tmp_path: Path) -> None:
    """A file written before this fix may already hold one. Rewriting another key must not turn it
    into a line — the writer reads lines exactly as python-dotenv does, on newlines only."""
    from dotenv import dotenv_values

    env = tmp_path / ".env"
    env.write_bytes(
        f"CHIMERA_REACH=read_only\nCHIMERA_SANDBOX_IMAGE=a {SMUGGLED}\n".encode()
    )
    write_env_value(env, "CHIMERA_DEFAULT_MODEL", "x/y")
    assert dotenv_values(env).get("CHIMERA_REACH") == "read_only"
    assert env_lines(env.read_text(encoding="utf-8"))[0] == "CHIMERA_REACH=read_only"
    assert len(env_lines(env.read_text(encoding="utf-8"))) == 3
    # And a CRLF file keeps its lines: the `\r` is a line ending, not part of the value.
    crlf = tmp_path / "crlf.env"
    crlf.write_bytes(b"A=1\r\nB=2\r\n")
    set_env_entry(crlf, "A", "A=3")
    assert env_lines(crlf.read_bytes().decode("utf-8")) == ["A=3", "B=2"]
    assert dotenv_values(crlf) == {"A": "3", "B": "2"}


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true")
    for key in ("CHIMERA_SANDBOX_IMAGE", "CHIMERA_DEFAULT_MODEL", "CHIMERA_REACH"):
        monkeypatch.setenv(key, "before")
    monkeypatch.setenv("CHIMERA_REACH", "read_only")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    (tmp_path / ".env").write_text("CHIMERA_REACH=read_only\n", encoding="utf-8")
    return app


def test_no_http_writer_takes_a_splitting_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's save, the bridge's edit of a key it may write, and a bridge suggestion: each is
    refused for every character `splitlines` splits on, and the file is untouched."""
    app = _app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        for ch in SPLITTERS:
            value = f"python:3.12-slim{ch}{SMUGGLED}"
            owner = client.patch("/api/config", json={"CHIMERA_SANDBOX_IMAGE": value})
            assert owner.status_code == 400, (repr(ch), owner.text)
            bridge = client.post(
                "/api/bridge/call",
                json={"route": "settings.edit", "body": {"CHIMERA_SANDBOX_IMAGE": value}},
                headers=headers,
            )
            assert bridge.json()["status"] == 400, (repr(ch), bridge.text)
            suggested = client.post(
                "/api/bridge/call",
                json={
                    "route": "settings.edit",
                    "body": {"CHIMERA_DEFAULT_MODEL": f"openrouter/a/b{ch}CHIMERA_APPROVAL=never"},
                },
                headers=headers,
            )
            assert suggested.status_code == 400, (repr(ch), suggested.text)
        assert client.get("/api/approvals").json() == []
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "CHIMERA_REACH=read_only\n"
    get_settings.cache_clear()
    assert os.environ["CHIMERA_REACH"] == "read_only"


def test_a_card_that_carries_one_anyway_is_never_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A card written before this fix, or by hand: the owner's yes re-runs the same check and
    writes nothing."""
    from chimera.api.config_api import setting_value
    from chimera.governance import setting_suggestions

    app = _app(tmp_path, monkeypatch)
    current = setting_value(get_settings(), "CHIMERA_DEFAULT_MODEL")
    request_id = setting_suggestions.suggest(
        tmp_path / "home",
        [
            setting_suggestions.Change(
                "CHIMERA_DEFAULT_MODEL", current, "openrouter/a/b CHIMERA_APPROVAL=never"
            )
        ],
        suggested_by="desktop_bridge",
    )
    with TestClient(app) as client:
        card = client.get("/api/approvals").json()[0]
        answered = client.post(
            f"/api/approvals/{request_id}",
            json={"approved": True, "digest": card["suggestion"]["digest"]},
        )
    assert answered.json()["outcome"] == "invalid"
    assert "CHIMERA_APPROVAL" not in (tmp_path / ".env").read_text(encoding="utf-8")
    get_settings.cache_clear()
