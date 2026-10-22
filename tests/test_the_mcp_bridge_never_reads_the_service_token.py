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

Since S2 ``chimera code`` has a second transport, the owner's service, and prefers it when one runs.
That is a PERSON's command reaching the owner's door, and it lives in its own module
(``chimera/cli/code_service.py``). The invariant this file keeps is the bridge's side of it: the
bridge client in ``code_cmd.py`` (``AppCode``) still never reads the service files, nothing on the
MCP side imports the service transport — so no MCP tool can reach the owner's token through it —
and the service transport never reads the bridge's file, so the two tokens never meet.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str], server_run: Any = None
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

    monkeypatch.setattr("uvicorn.Server.run", server_run or serving)
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
def test_in_process_service_run_returns_while_an_unrelated_thread_is_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = threading.Event()
    worker = threading.Thread(target=stop.wait, name="test-unrelated-thread", daemon=False)

    def serving(_self: object, **_kw: object) -> None:
        worker.start()

    try:
        result, _seen = _serve(
            monkeypatch, tmp_path,
            ["service", "run", "--port", "0", "--no-memory", "--no-cron"],
            server_run=serving,
        )
        assert result.exit_code == 0, result.output
        assert worker.is_alive(), "the thread should still be alive when CliRunner returns"
        assert True, "reaching this line proves the in-process command did not force-exit pytest"
    finally:
        stop.set()
        if worker.ident is not None:
            worker.join(timeout=2)
        assert not worker.is_alive(), "test worker did not stop cleanly"

def test_service_run_entrypoint_exits_after_a_stray_non_daemon_thread(tmp_path: Path) -> None:
    startup = tmp_path / "python-startup"
    startup.mkdir()
    (startup / "sitecustomize.py").write_text(
        "import threading, time, uvicorn\n"
        "def serving(_self, **_kwargs):\n"
        "    threading.Thread(target=lambda: time.sleep(120), name='test-stray', daemon=False).start()\n"
        "uvicorn.Server.run = serving\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["CHIMERA_HOME"] = str(tmp_path / "home")
    env["CHIMERA_SERVICE_DIR"] = str(tmp_path / "service")
    env["CHIMERA_SERVICE_CHANNEL"] = "dev"
    env["PYTHONPATH"] = str(startup) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("CHIMERA_SERVER_TOKEN", None)

    completed = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "service", "run", "--port", "0", "--no-memory", "--no-cron"],
        env=env, capture_output=True, text=True, timeout=15, check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


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


#: The MCP side: the server Claude talks to, and the bridge's own route table and discovery.
MCP_SIDE = (
    "chimera/server/desktop_mcp.py",
    "chimera/api/bridge_routes.py",
    "chimera/api/bridge_discovery.py",
)


def _imports(rel: str) -> set[str]:
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    return {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}


@pytest.mark.parametrize("rel", MCP_SIDE)
def test_nothing_on_the_mcp_side_reaches_the_terminals_service_transport(rel: str) -> None:
    """`chimera code new` and the service transport are the owner's: never an MCP tool (S2)."""
    names = _imports(rel)
    assert not any(n.endswith("code_service") or n.endswith("code_cmd") for n in names), rel
    assert "code_service" not in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_the_service_transport_never_reads_the_bridge_file() -> None:
    names = _imports("chimera/cli/code_service.py")
    assert not any("bridge_discovery" in n or "desktop_bridge" in n for n in names), names
    assert "desktop-bridge" not in (ROOT / "chimera/cli/code_service.py").read_text(encoding="utf-8")


def test_the_bridge_route_table_gained_no_service_route() -> None:
    from chimera.api.bridge_routes import ROUTES

    assert not [name for name in ROUTES if "service" in name], sorted(ROUTES)
    assert not [r for r in ROUTES.values() if "/api/service" in str(getattr(r, "path", ""))]
