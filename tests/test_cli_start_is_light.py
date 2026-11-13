"""``chimera --help`` must not import the agent core or the server stack.

Starting the CLI took ~2 s on a laptop against 0.1 s for a bare interpreter. One import in
``chimera/cli/code_cmd.py`` (``chimera.server.desktop_mcp``, for three names) ran
``chimera/server/__init__``, which pulls the Discord adapter, the gateway, the code session and the
agent loop: every ``chimera version`` paid for code that only ``serve`` and ``agent`` use. Nothing
fails when that comes back, the command just gets slower, so this asserts it in a fresh process.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

HEAVY = (
    "chimera.core.agent",
    "chimera.server.discord_adapter",
    "chimera.server.desktop_mcp",
    "httpx",
)

_PROBE = """
import json, sys
from typer.testing import CliRunner
from chimera.cli.main import app

result = CliRunner().invoke(app, ["--help"])
assert result.exit_code == 0, result.output
print(json.dumps({"loaded": sorted(m for m in sys.modules if m in %r)}))
"""


def _loaded_after_help() -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE % (set(HEAVY),)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])["loaded"]


def test_help_does_not_import_the_agent_or_the_server_stack() -> None:
    assert _loaded_after_help() == []


def test_the_bridge_transport_is_still_reachable_from_the_old_module() -> None:
    from chimera.api import bridge_transport
    from chimera.server import desktop_mcp

    assert desktop_mcp.urllib_call is bridge_transport.urllib_call
    assert desktop_mcp.NOT_RUNNING == bridge_transport.NOT_RUNNING


@pytest.mark.parametrize("name", HEAVY)
def test_the_heavy_names_are_what_this_test_thinks_they_are(name: str) -> None:
    # Guards the guard: a renamed module would make the assertion above pass on nothing.
    import importlib

    assert importlib.util.find_spec(name) is not None
