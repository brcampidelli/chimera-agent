"""Claude's door and the owner's door stay two doors (S1, decision D2).

The desktop bridge (``desktop-bridge.json``) carries a token that is Claude's: short-lived and
limited to a table of routes. The service record (``service-<channel>.json``) and ``service.token``
carry the owner's, which opens the whole API. If the MCP client or ``chimera code`` ever fell back
to the service record — "the bridge is off, but here is a URL and a token" — the switch the owner
uses to keep Claude out would stop meaning anything. So:

* nothing on the bridge's client side reads the service files, statically or at run time;
* the bridge refuses them as files, at every tier;
* a service with no window open publishes no bridge, even with "Allow Claude" on — the bridge is
  for a window the owner has open, and the desktop shell publishes it when one attaches;
* ``--standalone`` publishes nothing, and ``chimera app`` with no flag is exactly what it was.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.api.bridge_routes import is_secret_file
from chimera.config import get_settings
from chimera.core.service_record import load_or_create_token, new_record, write_record

ROOT = Path(__file__).resolve().parent.parent
#: Every module on the bridge's client side: the MCP server, the terminal's `code`/`sessions`, and
#: the discovery reader they share.
BRIDGE_CLIENTS = (
    "chimera/server/desktop_mcp.py",
    "chimera/cli/code_cmd.py",
    "chimera/cli/sessions_cmd.py",
    "chimera/api/bridge_discovery.py",
)


@pytest.mark.parametrize("rel", BRIDGE_CLIENTS)
def test_no_bridge_client_imports_or_names_the_service_files(rel: str) -> None:
    source = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any("service_record" in name or "service_watch" in name for name in imported), rel
    assert "service-" not in source and "service.token" not in source, rel


def test_with_only_a_service_record_the_bridge_clients_find_no_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same folder for both files, a valid record with a URL and a token in it, and no bridge."""
    from chimera.api.bridge_discovery import read_discovery
    from chimera.server import desktop_mcp

    monkeypatch.setenv("CHIMERA_SERVICE_DIR", str(tmp_path))
    monkeypatch.setenv("CHIMERA_BRIDGE_DIR", str(tmp_path))
    write_record(new_record(url="http://127.0.0.1:1", token="owner", home=tmp_path), tmp_path / "service-dev.json")
    load_or_create_token(tmp_path)
    opened: list[str] = []
    real_read_text = Path.read_text

    def spy(self: Path, *a: Any, **kw: Any) -> str:
        opened.append(self.name)
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy)

    assert read_discovery() is None
    assert desktop_mcp._read_discovery() is None
    assert all(not name.startswith("service") for name in opened), opened


@pytest.mark.parametrize(
    "path", ["service-dev.json", "service-stable.json", "~/.chimera/service.token", "desktop-install.json"]
)
def test_the_bridge_treats_the_service_files_as_credentials(path: str) -> None:
    assert is_secret_file(path)


def test_an_ordinary_file_named_service_is_not_a_credential() -> None:
    assert not is_secret_file("service.py") and not is_secret_file("services.json")


def _serve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str]
) -> tuple[Any, dict[str, Any]]:
    """Run a command up to the point the server would serve, and look around from there."""
    from chimera.cli.main import app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_SERVICE_DIR", str(tmp_path / "svc"))
    monkeypatch.setenv("CHIMERA_SERVICE_CHANNEL", "dev")
    monkeypatch.setenv("CHIMERA_BRIDGE_DIR", str(tmp_path / "svc"))
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "1")  # "Allow Claude" on
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.delenv("CHIMERA_SERVER_TOKEN")  # set by service mode; restored by monkeypatch
    get_settings.cache_clear()
    seen: dict[str, Any] = {}

    def serving(_self: object, **_kw: object) -> None:
        svc = tmp_path / "svc"
        seen["files"] = sorted(p.name for p in svc.iterdir()) if svc.exists() else []
        seen["record"] = (
            json.loads((svc / "service-dev.json").read_text(encoding="utf-8"))
            if (svc / "service-dev.json").exists() else None
        )
        seen["token"] = get_settings().server_token

    monkeypatch.setattr("uvicorn.Server.run", serving)
    monkeypatch.setattr("chimera.cli.commands.serve._start_cron_daemon", lambda *a, **k: None)
    try:
        result = CliRunner().invoke(app, args)
    finally:
        get_settings.cache_clear()
    return result, seen


def test_a_service_without_a_window_publishes_no_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, seen = _serve(monkeypatch, tmp_path, ["service", "run", "--port", "0", "--no-memory", "--no-cron"])

    assert result.exit_code == 0, result.output
    assert seen["files"] == ["service-dev.json"], "a service with no window published the bridge"
    assert seen["record"]["mode"] == "service" and seen["record"]["token"] == seen["token"]
    assert not (tmp_path / "svc" / "service-dev.json").exists(), "the record outlived the server"


def test_standalone_publishes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, seen = _serve(
        monkeypatch, tmp_path, ["app", "--standalone", "--no-open", "--no-memory", "--no-cron", "--port", "0"]
    )

    assert result.exit_code == 0, result.output
    assert seen["files"] == [] and seen["record"] is None


def test_chimera_app_with_no_new_flag_is_what_it_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No service record, no owner token forced on the window's backend, and the bridge published
    when the switch is on — exactly as before S1."""
    result, seen = _serve(monkeypatch, tmp_path, ["app", "--no-open", "--no-memory", "--no-cron", "--port", "0"])

    assert result.exit_code == 0, result.output
    assert seen["files"] == ["desktop-bridge.json"] and seen["record"] is None
    assert not seen["token"], "chimera app started requiring a token the window cannot send yet"
