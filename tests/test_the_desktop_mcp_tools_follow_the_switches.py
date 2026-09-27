"""`chimera mcp desktop`: the tool list follows the owner's switches, and every tool is one bridge call.

The client half of the desktop bridge (`chimera/server/desktop_mcp.py`), driven through its two seams
— reading the discovery file and making the HTTP call — so nothing here needs the `mcp` SDK, a
socket or a model. The last tests close the loop against a REAL app through `TestClient`.

The exact tool-name sets are asserted on purpose: a tool that approves, changes posture or edits
settings appearing in the operate list is the failure this file exists to catch, and a subset check
would let it through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import ROUTES, areas
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun
from chimera.server.desktop_mcp import NOT_RUNNING, DesktopMCP

TOKEN = "tok-" + "Q" * 40
OPERATE_TOOLS = {
    "desktop_status",
    "desktop_send",
    "desktop_job",
    "desktop_projects",
    "desktop_conversations",
    "desktop_works",
    "desktop_runs",
    "desktop_approvals",
    "desktop_memory",
    "desktop_chat",
    "desktop_agents",
    "desktop_orchestration",
    "desktop_lifecycle",
    "desktop_kanban",
    "desktop_spec_projects",
    "desktop_cron",
    "desktop_skills",
    "desktop_files",
    "desktop_git",
    "desktop_planning",
    "desktop_shell_jobs",
    "desktop_insights",
    "desktop_app",
}
FULL_TOOLS = {"desktop_approve", "desktop_settings"}


class FakeHttp:
    """Records every call and answers from a script."""

    def __init__(self, answer: Any = None, status: int | None = 200) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.answer = answer
        self.status = status

    def __call__(
        self, method: str, url: str, token: str, body: Any, timeout: float
    ) -> tuple[int | None, Any]:
        assert token == TOKEN
        self.calls.append((method, url, body))
        if callable(self.answer):
            return self.status, self.answer(method, url, body)
        return self.status, self.answer


def _found(full: bool = False) -> dict[str, Any]:
    return {"url": "http://127.0.0.1:65009", "token": TOKEN, "pid": 1, "version": "x", "full": full}


def _names(mcp: DesktopMCP) -> set[str]:
    return {spec["name"] for spec in mcp.tool_specs()}


# ---- the list -----------------------------------------------------------------------------------


def test_the_exact_tool_sets_off_on_and_full() -> None:
    assert _names(DesktopMCP(discover=lambda: None, http=FakeHttp())) == OPERATE_TOOLS
    assert _names(DesktopMCP(discover=_found, http=FakeHttp())) == OPERATE_TOOLS
    assert (
        _names(DesktopMCP(discover=lambda: _found(True), http=FakeHttp()))
        == OPERATE_TOOLS | FULL_TOOLS
    )


def test_without_full_control_no_listed_action_approves_or_edits_settings() -> None:
    specs = DesktopMCP(discover=_found, http=FakeHttp()).tool_specs()
    listed = {
        f"{spec['name'].removeprefix('desktop_')}.{action}"
        for spec in specs
        for action in spec["inputSchema"]["properties"].get("action", {}).get("enum", [])
    }
    full_routes = {rid for rid, r in ROUTES.items() if r.tier == "full"}
    assert listed and not listed & full_routes
    send = next(s for s in specs if s["name"] == "desktop_send")
    assert "posture" not in send["inputSchema"]["properties"]
    assert "allow_host_exec" not in send["inputSchema"]["properties"]


def test_with_full_control_send_takes_a_posture() -> None:
    specs = DesktopMCP(discover=lambda: _found(True), http=FakeHttp()).tool_specs()
    send = next(s for s in specs if s["name"] == "desktop_send")
    assert {"posture", "allow_host_exec"} <= set(send["inputSchema"]["properties"])


def test_no_tool_description_or_schema_carries_the_token() -> None:
    specs = DesktopMCP(discover=lambda: _found(True), http=FakeHttp()).tool_specs()
    assert TOKEN not in json.dumps(specs)


# ---- not running --------------------------------------------------------------------------------


def test_every_tool_says_the_app_is_not_running_when_there_is_no_file() -> None:
    http = FakeHttp()
    mcp = DesktopMCP(discover=lambda: None, http=http)
    for name in OPERATE_TOOLS | FULL_TOOLS:
        assert mcp.dispatch(name, {"action": "list", "message": "hi", "job_id": "j"}) == NOT_RUNNING
    assert http.calls == []


def test_an_unreachable_app_reads_the_same() -> None:
    mcp = DesktopMCP(discover=_found, http=FakeHttp(status=None))
    assert mcp.dispatch("desktop_status", {}) == NOT_RUNNING


def test_an_unknown_tool_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        DesktopMCP(discover=_found, http=FakeHttp()).dispatch("desktop_approve_everything", {})


# ---- dispatch -----------------------------------------------------------------------------------


def test_each_listed_action_calls_exactly_its_route() -> None:
    http = FakeHttp({"route": "", "status": 200, "data": {"ok": 1}})
    mcp = DesktopMCP(discover=lambda: _found(True), http=http)
    for spec in mcp.tool_specs():
        for action in spec["inputSchema"]["properties"].get("action", {}).get("enum", []):
            if (spec["name"], action) == ("desktop_runs", "read"):
                continue
            http.calls.clear()
            mcp.dispatch(spec["name"], {"action": action, "params": {"x": "1"}, "body": {"a": 1}})
            ((method, url, body),) = http.calls
            assert (method, url.rsplit("/api/", 1)[1]) == ("POST", "bridge/call")
            assert body["route"] == f"{spec['name'].removeprefix('desktop_')}.{action}"
            assert body["params"] == {"x": "1"} and body["body"] == {"a": 1}


def test_a_full_tool_is_refused_by_the_client_too_and_nothing_is_sent() -> None:
    http = FakeHttp()
    mcp = DesktopMCP(discover=_found, http=http)
    for name in FULL_TOOLS:
        assert "Full control" in mcp.dispatch(name, {"action": "approval"})
    assert http.calls == []


def test_send_drops_a_posture_the_owner_did_not_grant() -> None:
    http = FakeHttp(
        {
            "route": "conversations.send",
            "status": 200,
            "job": {"done": True, "result": {"answer": "hi"}},
        }
    )
    DesktopMCP(discover=_found, http=http).dispatch(
        "desktop_send",
        {"message": "go", "posture": {"reach": "workspace_shell"}, "allow_host_exec": True},
    )
    sent = http.calls[0][2]
    assert sent["route"] == "conversations.send" and sent["body"] == {"message": "go"}

    http.calls.clear()
    DesktopMCP(discover=lambda: _found(True), http=http).dispatch(
        "desktop_send", {"message": "go", "posture": {"reach": "workspace_shell"}}
    )
    assert http.calls[0][2]["body"]["posture"] == {"reach": "workspace_shell"}


def test_send_returns_the_reply_and_the_receipt() -> None:
    job = {
        "job_id": "j1",
        "done": True,
        "session_id": "s1",
        "turn_id": "t1",
        "next": 3,
        "events": [],
        "result": {
            "answer": "It is fixed.",
            "model": "m/x",
            "usd": 0.0123,
            "system_sha": "abc123",
            "prompt_tokens": 10,
            "completion_tokens": 5,
        },
    }
    text = DesktopMCP(discover=_found, http=FakeHttp({"status": 200, "job": job})).dispatch(
        "desktop_send", {"message": "fix it"}
    )
    assert "It is fixed." in text
    for fact in ('"system_sha": "abc123"', '"usd": 0.0123', '"model": "m/x"', '"session_id": "s1"'):
        assert fact in text


def test_send_that_stops_for_an_approval_says_it_is_waiting_for_the_owner() -> None:
    job = {
        "job_id": "j2",
        "done": False,
        "waiting_for_approval": True,
        "session_id": "s1",
        "pending_approvals": [
            {"id": "q1", "action": "run_shell: rm -rf build", "reason": "destructive"}
        ],
        "events": [],
        "next": 2,
        "text": "",
    }
    text = DesktopMCP(discover=_found, http=FakeHttp({"status": 200, "job": job})).dispatch(
        "desktop_send", {"message": "clean up", "wait_seconds": 999}
    )
    assert text.startswith("WAITING FOR THE OWNER'S APPROVAL")
    assert "run_shell: rm -rf build" in text and "desktop_job job_id=j2" in text


def test_the_wait_is_bounded_whatever_the_client_asks() -> None:
    http = FakeHttp({"status": 200, "job": {"done": True}})
    DesktopMCP(discover=_found, http=http).dispatch(
        "desktop_send", {"message": "x", "wait_seconds": 10_000}
    )
    assert http.calls[0][2]["wait_seconds"] == 300.0


def test_runs_read_picks_one_receipt_by_index() -> None:
    receipts = [{"task": "newest"}, {"task": "older"}]
    http = FakeHttp({"status": 200, "data": receipts})
    text = DesktopMCP(discover=_found, http=http).dispatch(
        "desktop_runs", {"action": "read", "params": {"index": 1}}
    )
    assert json.loads(text) == {"task": "older"}
    assert http.calls[0][2]["route"] == "runs.list"


def test_the_client_scrubs_what_the_app_returns() -> None:
    leaky = {
        "status": 200,
        "data": {"providers": [{"set": True, "hint": "…wxyz"}], "note": f"t={TOKEN}"},
    }
    text = DesktopMCP(discover=_found, http=FakeHttp(leaky)).dispatch(
        "desktop_app", {"action": "config"}
    )
    assert TOKEN not in text and "wxyz" not in text and '"set": true' in text


def test_refusals_and_a_stale_token_read_as_sentences() -> None:
    refused = DesktopMCP(
        discover=_found, http=FakeHttp({"detail": "needs Full control"}, status=403)
    )
    assert refused.dispatch("desktop_memory", {"action": "search"}).startswith("Refused by the app")
    stale = DesktopMCP(discover=_found, http=FakeHttp({"detail": "unauthorized"}, status=401))
    assert "token" in stale.dispatch("desktop_status", {})


# ---- against a real app -------------------------------------------------------------------------


def _through(client: TestClient) -> Any:
    def http(
        method: str, url: str, token: str, body: Any, timeout: float
    ) -> tuple[int | None, Any]:
        path = "/" + url.split("/", 3)[3]
        response = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
        return response.status_code, response.json()

    return http


def test_end_to_end_against_a_running_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.api.desktop_bridge import read_discovery

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "false")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach("http://127.0.0.1:65010")

    with TestClient(app) as client:
        mcp = DesktopMCP(discover=read_discovery, http=_through(client))
        status = json.loads(mcp.dispatch("desktop_status", {}))
        added = mcp.dispatch(
            "desktop_memory", {"action": "add", "body": {"content": "the build uses uv"}}
        )
        found = mcp.dispatch("desktop_memory", {"action": "search", "params": {"q": "uv"}})
        # Forged past the list: the app still refuses it.
        forged = mcp.http(
            "POST",
            "http://127.0.0.1:65010/api/bridge/call",
            app.state.desktop_bridge.token,
            {
                "route": "approve.approval",
                "params": {"request_id": "q1"},
                "body": {"approved": True},
            },
            5.0,
        )

        client.patch("/api/config", json={"CHIMERA_DESKTOP_BRIDGE": "false"})
        after = mcp.dispatch("desktop_status", {})
    assert status["tier"] == "operate" and "error" not in added
    assert "the build uses uv" in found
    assert forged[0] == 403
    assert after == NOT_RUNNING
    assert _names(mcp) == OPERATE_TOOLS
    assert set(areas("full")) == {name.removeprefix("desktop_") for name in FULL_TOOLS}
    get_settings.cache_clear()


def test_the_sdk_server_lists_the_same_tools_and_answers_a_call() -> None:
    """The SDK wiring, where it is installed: listing is the switch-aware list, calling is dispatch."""
    import asyncio

    types = pytest.importorskip("mcp.types")
    http = FakeHttp({"status": 200, "data": [{"path": "/p", "alias": ""}]})
    server = DesktopMCP(discover=_found, http=http).build()

    listed = asyncio.run(
        server.request_handlers[types.ListToolsRequest](types.ListToolsRequest(method="tools/list"))
    )
    called = asyncio.run(
        server.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="desktop_projects", arguments={"action": "list"}
                ),
            )
        )
    )
    assert {tool.name for tool in listed.root.tools} == OPERATE_TOOLS
    assert '"path": "/p"' in called.root.content[0].text
