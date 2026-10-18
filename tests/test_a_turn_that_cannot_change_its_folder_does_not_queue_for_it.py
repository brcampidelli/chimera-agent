"""A coding turn that cannot change its folder does not wait for the folder lock, nor hold it (M20).

Observed 2026-10-09/10 while running a research task through the desktop bridge: conversations that
only read files queued one behind another in the same folder, because every turn took the folder's
lock. The lock exists so that a snapshot, a verification and an undo describe one turn's edits; a
turn given no write and no exec tool makes none.

Decided from the tools the turn was actually given, and "can change" whenever that cannot be shown:
an external agent, an agent whose tools cannot be listed, the deferred proxies, an MCP tool.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.code_api import turn_can_change_the_folder
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession
from chimera.tools import ToolRegistry
from chimera.tools.base import Tool

TIMEOUT = 10.0


class _Named(Tool):
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, name: str, *, open_arguments: bool = False) -> None:
        self.name = name
        self.description = name
        self.open_arguments = open_arguments

    def run(self, **kwargs: Any) -> str:
        return "ok"


class _Agent:
    def __init__(self, tools: ToolRegistry | None) -> None:
        if tools is not None:
            self.tools = tools


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def test_only_a_turn_shown_to_be_unable_to_change_the_folder_skips_the_lock() -> None:
    reads = _registry(_Named("read_file"), _Named("grep"), _Named("list_dir"))
    assert turn_can_change_the_folder(_Agent(reads), None) is False

    assert turn_can_change_the_folder(_Agent(_registry(_Named("write_file"))), None) is True
    assert turn_can_change_the_folder(_Agent(_registry(_Named("run_shell"))), None) is True
    assert turn_can_change_the_folder(_Agent(_registry(_Named("tool_call"))), None) is True
    assert turn_can_change_the_folder(
        _Agent(_registry(_Named("fs_server_write", open_arguments=True))), None
    ) is True
    assert turn_can_change_the_folder(_Agent(None), None) is True  # tools that cannot be listed
    assert turn_can_change_the_folder(_Agent(reads), "codex") is True  # an external agent edits


# ----------------------------------------------------------------------------- the route


class _Turn:
    """Records which turns entered, keeps the registry it was given, and can be held open."""

    hold = threading.Event()
    held = threading.Event()
    entered: list[str] = []

    tools_of: dict[str, list[str]] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if len(args) > 1 and isinstance(args[1], ToolRegistry):
            self.tools = args[1]

    def run(self, task: str, should_stop: Any = None, **_kw: Any) -> AgentResult:
        label = task.split(" [")[0].split("\n")[-1]
        tools = getattr(self, "tools", None)
        type(self).tools_of[label] = list(tools.names()) if tools is not None else []
        type(self).entered.append(label)
        if "hold" in task:
            type(self).held.set()
            deadline = time.monotonic() + TIMEOUT
            while not type(self).hold.is_set() and time.monotonic() < deadline:
                if should_stop is not None and should_stop():
                    break
                time.sleep(0.01)
        return AgentResult(
            answer="done", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
        )


@pytest.fixture
def turn() -> Any:
    _Turn.hold.clear()
    _Turn.held.clear()
    _Turn.entered = []
    _Turn.tools_of = {}
    yield _Turn
    _Turn.hold.set()


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Turn, raising=True)
    ws = tmp_path / "default-ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    return TestClient(build_api_app(lambda: ChatSession(_Turn()), workspace=ws, settings=settings))


def _send(client: TestClient, folder: Path, message: str, *, read_only: bool) -> str:
    body: dict[str, Any] = {
        "message": f"{message} [{folder}]", "workspace": str(folder), "stream": False,
    }
    if read_only:
        body["posture"] = {"reach": "read_only", "approval": "never"}
    return client.post("/api/code/turn", json=body).text


def test_a_read_only_turn_runs_while_a_writing_turn_holds_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Any
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch)
    writer = threading.Thread(
        target=lambda: _send(client, folder, "hold", read_only=False), daemon=True
    )
    writer.start()
    assert turn.held.wait(TIMEOUT)

    reply = _send(client, folder, "read", read_only=True)

    assert "folder_busy" not in reply
    assert turn.entered == ["hold", "read"]
    turn.hold.set()
    writer.join(TIMEOUT)


def test_a_read_only_turn_does_not_make_a_writing_turn_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Any
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch)
    reader = threading.Thread(
        target=lambda: _send(client, folder, "hold", read_only=True), daemon=True
    )
    reader.start()
    assert turn.held.wait(TIMEOUT)

    reply = _send(client, folder, "write", read_only=False)

    assert "folder_busy" not in reply
    assert turn.entered == ["hold", "write"]
    turn.hold.set()
    reader.join(TIMEOUT)


def test_two_writing_turns_still_take_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Any
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch)
    replies: dict[str, str] = {}
    first = threading.Thread(
        target=lambda: replies.setdefault("first", _send(client, folder, "hold", read_only=False)),
        daemon=True,
    )
    first.start()
    assert turn.held.wait(TIMEOUT)
    second = threading.Thread(
        target=lambda: replies.setdefault("second", _send(client, folder, "write", read_only=False)),
        daemon=True,
    )
    second.start()
    time.sleep(0.4)
    assert turn.entered == ["hold"]
    turn.hold.set()
    first.join(TIMEOUT)
    second.join(TIMEOUT)
    assert "folder_busy" in replies["second"]


# ----------------------------------------------------------------------------- C10, study 34
#
# Observed 2026-10-18: three research turns sent through the bridge to one folder ran one at a
# time. A read-only turn is exactly the turn that needs no queue; these pin the interplay with
# writers in both orders, and that a read-only turn still has no way to write.


def _wait_entered(turn: Any, count: int) -> None:
    deadline = time.monotonic() + TIMEOUT
    while len(turn.entered) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(turn.entered) >= count, turn.entered


def test_read_only_turns_run_together_beside_a_writer_and_the_next_writer_still_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Any
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch)
    replies: dict[str, str] = {}

    def send(label: str, *, read_only: bool) -> threading.Thread:
        def go() -> None:
            replies[label] = _send(client, folder, label, read_only=read_only)

        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        return thread

    writer = send("hold-writer", read_only=False)
    _wait_entered(turn, 1)
    readers = [send(f"hold-reader-{i}", read_only=True) for i in (1, 2)]
    _wait_entered(turn, 3)  # both readers are INSIDE their loops while the writer holds the folder
    quick = _send(client, folder, "read", read_only=True)
    assert "folder_busy" not in quick

    second_writer = send("write", read_only=False)
    time.sleep(0.4)
    # Readers around or not, two writers in one folder still take turns.
    assert "write" not in turn.entered
    assert sorted(turn.entered) == sorted(["hold-writer", "hold-reader-1", "hold-reader-2", "read"])

    turn.hold.set()
    for thread in (writer, *readers, second_writer):
        thread.join(TIMEOUT)
    assert turn.entered[-1] == "write"
    assert "folder_busy" in replies["write"]
    assert all("folder_busy" not in replies[f"hold-reader-{i}"] for i in (1, 2))


def test_a_writer_that_arrives_while_read_only_turns_run_is_not_queued_behind_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Any
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch)
    readers = [
        threading.Thread(
            target=lambda i=i: _send(client, folder, f"hold-reader-{i}", read_only=True),
            daemon=True,
        )
        for i in (1, 2)
    ]
    for thread in readers:
        thread.start()
    _wait_entered(turn, 2)

    # Readers never hold the lock, so the writer takes it at once — and holds it alone.
    folder_lock = client.app.state.folder_locks.lock(folder)  # type: ignore[attr-defined]
    assert not folder_lock.locked()
    reply = _send(client, folder, "write", read_only=False)
    assert "folder_busy" not in reply
    assert turn.entered[-1] == "write"
    turn.hold.set()
    for thread in readers:
        thread.join(TIMEOUT)


def test_a_read_only_turn_that_skips_the_lock_has_no_way_to_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Any
) -> None:
    from chimera.governance.ledger import EXEC_TOOLS, WRITE_TOOLS

    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch)
    _send(client, folder, "read", read_only=True)
    _send(client, folder, "write", read_only=False)

    given = set(turn.tools_of["read"])
    assert given, "the read-only turn was given no tools at all: the check below would be vacuous"
    assert not given & (WRITE_TOOLS | EXEC_TOOLS)
    assert "mcp_call" not in given
    # The comparison turn shows the registry does carry write tools when the posture allows them.
    assert set(turn.tools_of["write"]) & WRITE_TOOLS


def test_the_deferral_proxy_is_looked_through_not_assumed_to_write() -> None:
    from chimera.tools.defer import defer_builtins

    reads, moved = defer_builtins(
        _registry(_Named("read_file"), _Named("grep"), _Named("scrape"), _Named("http_get"))
    )
    assert moved and "tool_call" in reads.names()
    # Every tool behind the proxy only reads: the proxy cannot change the folder either.
    assert turn_can_change_the_folder(_Agent(reads), None) is False

    writes, _ = defer_builtins(_registry(_Named("read_file"), _Named("scrape"), _Named("edit_batch")))
    assert "edit_batch" not in writes.names()  # behind the proxy, reachable through tool_call
    assert turn_can_change_the_folder(_Agent(writes), None) is True

    # A read-only posture's denials reach the proxy's catalogue, so it is a reader again.
    narrowed, _ = defer_builtins(
        _registry(_Named("read_file"), _Named("scrape"), _Named("edit_batch")),
        denied=frozenset({"edit_batch"}),
    )
    assert turn_can_change_the_folder(_Agent(narrowed), None) is False

    # The MCP proxy reaches tools whose effects are the server's: still a writer.
    mcp = _registry(_Named("read_file"), _Named("mcp_call"))
    assert turn_can_change_the_folder(_Agent(mcp), None) is True


class _Wrapper(Tool):
    """A governance layer around a tool that does not forward ``open_arguments`` itself."""

    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, inner: Tool) -> None:
        self.inner = inner
        self.name = inner.name
        self.description = inner.description

    def run(self, **kwargs: Any) -> str:
        return self.inner.run(**kwargs)


def test_an_mcp_tool_inside_a_wrapper_still_counts_as_a_writer() -> None:
    wrapped = _Wrapper(_Named("fs_server_read", open_arguments=True))
    assert turn_can_change_the_folder(_Agent(_registry(_Named("read_file"), wrapped)), None) is True
