"""`chimera mcp desktop` works where only the `mcp` extra is installed.

The published 0.62.2 bridge failed on its first `list_tools` with "No module named 'fastapi'". It
read the discovery file through `chimera.api.desktop_bridge`, which imports FastAPI at the top, and
Claude registers the bridge as `uvx --from "chimera-agent[mcp]"`, which has no FastAPI. The gate
never saw it, because the gate installs every extra. So this test forbids the web stack in a fresh
interpreter and drives the bridge the way Claude does.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_PROBE = r"""
import importlib.abc, json, sys

class _Forbid(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {"fastapi", "starlette", "uvicorn", "sse_starlette"}:
            raise ModuleNotFoundError(f"No module named '{name}'")
        return None

sys.meta_path.insert(0, _Forbid())
for name in list(sys.modules):
    if name.split(".")[0] in {"fastapi", "starlette", "uvicorn", "sse_starlette"}:
        del sys.modules[name]

from chimera.server.desktop_mcp import DesktopMCP

bridge = DesktopMCP()
names = [spec["name"] for spec in bridge.tool_specs()]
status = bridge.dispatch("desktop_status", {})
print(json.dumps({"names": names, "status": status}))
"""


def test_the_bridge_lists_its_tools_and_answers_status_without_the_web_stack(tmp_path: Path) -> None:
    env = {"CHIMERA_BRIDGE_DIR": str(tmp_path), "PYTHONUTF8": "1"}
    import os

    for key in ("PATH", "SYSTEMROOT", "PYTHONPATH", "HOME", "USERPROFILE", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    done = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, env=env, timeout=120
    )
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert "desktop_status" in out["names"] and "desktop_send" in out["names"]
    # No discovery file in the temporary directory: the answer is the honest "not running" one.
    assert "not running" in out["status"]
