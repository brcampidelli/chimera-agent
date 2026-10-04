"""A value Settings cannot read back from `.env` is never saved, by any writer.

The second adversarial review of 2026-10-04 (MEDIUM, pre-existing): the bridge's writable path ran
the allowlist and the per-key checks but not a parse, so it wrote
``CHIMERA_BROWSER_SITUATION='x"'`` (a 200) and the next ``get_settings()`` raised a ValidationError
— the app would not start until someone hand-edited ``.env``. The owner's screen had the same gap
for every key without a hand-written check. Now every save runs ``config_api.check_parses`` (inside
``check_updates``), which parses the candidate values from a scratch ``.env`` exactly as a restart
would, and a value that fails is refused with a 400 naming the key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.config_api import check_parses, check_updates
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun
from tests.test_every_env_reader_reads_back_what_was_written import _FileOnly

URL = "http://127.0.0.1:65013"
BAD: dict[str, str] = {
    "CHIMERA_BROWSER_SITUATION": 'x"',  # the review's value
    "CHIMERA_CASCADE": "maybe",
    "CHIMERA_SEMANTIC_MEMORY": "perhaps",
    "CHIMERA_ARCHIVE_AFTER_DAYS": "soon",
}


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true")
    for key in BAD:
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    (tmp_path / ".env").write_text("CHIMERA_REACH=read_only\n", encoding="utf-8")
    return app


@pytest.mark.parametrize("key", sorted(BAD))
def test_neither_the_bridge_nor_the_owner_saves_a_value_settings_cannot_read(
    key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        bridge = client.post(
            "/api/bridge/call",
            json={"route": "settings.edit", "body": {key: BAD[key]}},
            headers=headers,
        )
        owner = client.patch("/api/config", json={key: BAD[key]})
    # A suggestable key (CHIMERA_CASCADE) is refused before any card, as an HTTP 400 of the bridge
    # itself; a writable one by the forwarded save, as the inner status.
    assert bridge.status_code == 400 or bridge.json()["status"] == 400, bridge.text
    assert owner.status_code == 400, owner.text
    assert key in owner.json()["detail"]
    env = tmp_path / ".env"
    assert env.read_text(encoding="utf-8") == "CHIMERA_REACH=read_only\n"
    _FileOnly(_env_file=env)  # type: ignore[call-arg]  # still starts
    get_settings.cache_clear()


def test_the_parse_is_the_one_a_restart_makes() -> None:
    """List and JSON-shaped fields decode as at startup, so a value the app reads is not refused."""
    check_updates({"CHIMERA_FUSION_PANEL": "a/b,c/d", "CHIMERA_DAILY_USD_CAP": "2.5"})
    check_updates({"CHIMERA_CASCADE": "true", "CHIMERA_SANDBOX_IMAGE": '"quoted-start'})
    with pytest.raises(ValueError, match="CHIMERA_CASCADE"):
        check_parses({"CHIMERA_CASCADE": "maybe"})
    # A key no Settings field reads (a provider key) has nothing to parse.
    check_parses({"OPENROUTER_API_KEY": "sk-or-v1-x"})
