"""The memory of the last MCP Test is written from a thread pool, and three ways that lost or lied.

The test endpoint is synchronous, so FastAPI runs it on a worker thread and two Tests on two rows run
at the same time. Each loaded ``mcp_tests.json``, added its own record and saved — through the SAME
``mcp_tests.json.tmp`` — so the second save erased the first, or failed to rename a file the first had
already moved (swallowed as an OSError and logged, so nobody saw it).

The third is quieter. A Test still running when the owner replaces the server with a new token under
the same key names finishes AFTER the replacement has forgotten the old record, and wrote its result
back with a fingerprint that ignores env values — so the old token's "last tested ok" reappeared
beside the new token after a relaunch.
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from typing import Any

import pytest

from chimera.api import mcp_api
from chimera.integrations.mcp_config import McpServerConfig, add_server


def _home_with(tmp_path: Path, *cfgs: McpServerConfig) -> Path:
    home = tmp_path / "home"
    for cfg in cfgs:
        add_server(home / "mcp.json", cfg)
    return home


def test_two_tests_finishing_together_both_stay_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = McpServerConfig(name="notion", command="npx", args=["-y"])
    b = McpServerConfig(name="sentry", command="npx", args=["-y", "@sentry/mcp-server"])
    home = _home_with(tmp_path, a, b)

    # Both read the file before either writes it: the interleaving that loses one record. With the
    # lock, the second cannot read until the first has saved, so the barrier gives up and lets the
    # first through - the timeout is what keeps a correct version from deadlocking here.
    barreira = threading.Barrier(2, timeout=0.5)
    ler = mcp_api._load_tests

    def _load_then_wait(home: Path) -> dict[str, dict[str, Any]]:
        tests = ler(home)
        with contextlib.suppress(threading.BrokenBarrierError):
            barreira.wait()
        return tests

    monkeypatch.setattr(mcp_api, "_load_tests", _load_then_wait)
    threads = [
        threading.Thread(target=mcp_api._remember, args=(home, cfg), kwargs={"ok": True, "tool_count": n})
        for n, cfg in ((3, a), (5, b))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    monkeypatch.undo()

    servers = {s["name"]: s for s in mcp_api.list_servers(home)["servers"]}
    assert servers["notion"]["last_test"] is not None, "one Test's memory was erased by the other's"
    assert servers["sentry"]["last_test"] is not None, "one Test's memory was erased by the other's"


def test_a_test_that_ends_after_the_server_was_replaced_is_not_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = McpServerConfig(name="stripe", command="npx", args=["-y"], env={"TOKEN": "old"})
    home = _home_with(tmp_path, old)

    def _replaced_while_testing(cfg: McpServerConfig) -> list[dict[str, str]]:
        # The owner pastes a new token and presses Add while this Test is still connecting.
        mcp_api.add(home, "stripe", "npx", ["-y"], {"TOKEN": "new"})
        return [{"name": "list_customers", "description": ""}]

    monkeypatch.setattr(mcp_api, "_live_test", _replaced_while_testing)
    assert mcp_api.test_server(home, "stripe")["ok"] is True

    server = mcp_api.list_servers(home)["servers"][0]
    assert server["last_test"] is None, (
        "the old token's result was written back beside the new token - same key names, so the "
        "fingerprint could not tell them apart"
    )


def test_a_test_of_the_unchanged_server_is_still_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The control for the one above: the check must not throw away an honest result.
    cfg = McpServerConfig(name="stripe", command="npx", args=["-y"], env={"TOKEN": "same"})
    home = _home_with(tmp_path, cfg)
    monkeypatch.setattr(mcp_api, "_live_test", lambda _c: [{"name": "a", "description": ""}])

    mcp_api.test_server(home, "stripe")

    last = mcp_api.list_servers(home)["servers"][0]["last_test"]
    assert last is not None and last["tool_count"] == 1


def test_another_writers_temporary_file_does_not_stop_this_one_saving(tmp_path: Path) -> None:
    cfg = McpServerConfig(name="notion", command="npx", args=["-y"])
    home = _home_with(tmp_path, cfg)
    # What a second writer (the CLI, a second copy of the app) leaves at the shared temporary name
    # while it is mid-save. A fixed name collides with it; a file of this save's own does not.
    (home / "mcp_tests.json.tmp").mkdir()

    mcp_api._remember(home, cfg, ok=True, tool_count=2)

    assert mcp_api.list_servers(home)["servers"][0]["last_test"] is not None
    assert [p.name for p in home.glob("mcp_tests.json.*.tmp")] == [], "a temporary file was left"
