"""Manual's gate reads one table of writers, uses its own approver, and keeps its grants bounded.

The companion of ``test_manual_mode_asks_before_each_edit_and_command.py``: that file pins what the
owner sees on a turn; this one pins the things a review found around it. Every tool that writes in the
workspace is wrapped (and the next one fails a test instead of reaching a project); neither
``CHIMERA_APPROVAL_MODE=allow`` nor an old "always allow" rule answers a Manual question; "allow in this
conversation" is one tool and one file (or one exact command), can only be granted from the owner's
screen, and leaves nothing behind when the answer does not land; and a conversation stored in Manual
asks whatever its request body says.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.api.manual_gate import (  # noqa: E402
    MAX_KEY_CHARS,
    NEEDS_SCREEN,
    ManualAllowances,
    ManualEditGate,
    ManualScope,
    command_key,
    payload_asker,
)
from chimera.governance.ledger import WORKSPACE_WRITERS, WRITE_TOOLS  # noqa: E402
from tests.test_code_permission_screen_security import _bridged, _saved_conversation  # noqa: E402
from tests.test_manual_mode_asks_before_each_edit_and_command import (  # noqa: E402
    AUTO,
    MANUAL,
    SCREEN,
    _app,
    _Owner,
    _questions,
    _ScriptedAgent,
    _turn,
    _write,
)

_NO_SCREEN = NEEDS_SCREEN[:40]


def _assembled(tmp: Path, *, reach: str = "workspace_shell", attended: bool = True, **env: str) -> Any:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.api.posture import Posture
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    ws = tmp / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp / "home"), CHIMERA_MEMORY_BACKEND="json", **env)
    seams = CodeSeams(posture=Posture(reach=reach, approval="always", commands="asks", edits="asks"))  # type: ignore[arg-type]
    registry, _ = assemble_registry(
        seams, ws, settings, LLMGateway(), steps=4, surface="api:turn", manual_screen=attended,
    )
    return registry


def _is_gated(tool: Any) -> bool:
    for _ in range(24):
        if isinstance(tool, ManualEditGate):
            return True
        tool = getattr(tool, "inner", None) or getattr(tool, "_inner", None)
        if tool is None:
            return False
    return False


class _Stub:
    """A tool that records that it ran; the gate in front of it is what is under test."""

    description = "d"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    untrusted_output = False

    def __init__(self, name: str) -> None:
        self.name = name
        self.ran: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.ran.append(kwargs)
        return "ran"


def _gate(tmp: Path, name: str, asked: list[str], *, answer: bool = False, attended: bool = True) -> tuple[ManualEditGate, _Stub]:
    stub = _Stub(name)
    scope = ManualScope(ManualAllowances(), lambda: "s", lambda: attended)

    def ask(question: Any, action: str) -> bool:
        asked.append(action)
        return answer

    return ManualEditGate(stub, tmp, scope, ask, WORKSPACE_WRITERS[name]), stub  # type: ignore[arg-type]


# --- One table ----------------------------------------------------------------------------------------


def test_every_writer_in_the_manual_registry_is_wrapped(tmp_path: Path) -> None:
    registry = _assembled(tmp_path)
    held = [name for name in registry.names() if name in WORKSPACE_WRITERS]
    assert {"write_file", "edit_file", "apply_patch"} <= set(held), held
    unwrapped = [name for name in held if not _is_gated(registry.get(name))]
    assert unwrapped == [], f"these write in the workspace and do not ask in Manual: {unwrapped}"


def test_a_tool_that_writes_in_the_workspace_is_in_the_one_table() -> None:
    """The next writer fails here rather than in a user's project: any tool class that calls
    ``refuse_write`` (the write region's gate) has to be named in ``WORKSPACE_WRITERS``."""
    import ast

    root = Path(__file__).resolve().parent.parent / "chimera"
    found: dict[str, str] = {}
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef):
                continue
            name = next(
                (
                    b.value.value for b in node.body
                    if isinstance(b, ast.Assign) and len(b.targets) == 1
                    and getattr(b.targets[0], "id", "") == "name"
                    and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str)
                ),
                None,
            )
            if name and "refuse_write(" in (ast.get_source_segment(source, node) or ""):
                found[name] = path.name
    missing = {name: where for name, where in found.items() if name not in WORKSPACE_WRITERS}
    assert missing == {}, f"tools that write in the workspace but are not in the table: {missing}"
    assert {"render_chart", "generate_image", "download_media", "design", "browser"} <= set(found)


def test_the_ledger_and_the_read_only_reach_read_the_same_table(tmp_path: Path) -> None:
    from chimera.api.posture import Posture, resolve

    denied = set(resolve(Posture(reach="read_only")).deny_tools)
    assert set(WRITE_TOOLS) <= denied
    assert {
        "render_chart", "generate_image", "text_to_speech", "generate_audio", "generate_video", "download_media",
    } <= set(WRITE_TOOLS)
    # `design` and `browser` also read and propose, so a read-only reach keeps them and they refuse
    # their own writing action; Manual still asks for it.
    assert not ({"design", "browser"} & set(WRITE_TOOLS))
    assert {"design", "browser"} <= set(WORKSPACE_WRITERS)
    registry = _assembled(tmp_path, reach="read_only")
    assert not (set(registry.names()) & set(WRITE_TOOLS)), "a read-only turn still holds a writer"


def test_a_writer_that_names_no_file_asks_with_its_default_destination(tmp_path: Path) -> None:
    asked: list[str] = []
    gate, stub = _gate(tmp_path, "render_chart", asked)
    out = gate.run(spec={})
    assert asked == ["render_chart: chart.<format>"] and "declined" in out and stub.ran == []


def test_the_design_export_and_the_screenshot_ask_and_their_other_actions_do_not(tmp_path: Path) -> None:
    asked: list[str] = []
    design, design_stub = _gate(tmp_path, "design", asked)
    assert design.run(action="summary") == "ran" and asked == []
    assert "declined" in design.run(action="export", filename="logo")
    assert asked == ["design: logo"] and len(design_stub.ran) == 1
    browser, browser_stub = _gate(tmp_path, "browser", asked)
    assert browser.run(action="read") == "ran"
    assert "declined" in browser.run(action="screenshot", path="shots/a.png")
    assert asked[-1] == "browser: shots/a.png" and len(browser_stub.ran) == 1


def test_a_card_for_a_non_text_writer_shows_the_destination_and_no_diff(tmp_path: Path) -> None:
    asked: list[str] = []
    gate, _ = _gate(tmp_path, "generate_image", asked)
    gate.run(prompt="a cat", out="img/cat.png")
    assert asked == ["generate_image: img/cat.png"]


def test_an_mcp_payload_asks_before_it_lands_in_the_project(tmp_path: Path) -> None:
    from chimera.integrations.mcp_payloads import sink_for_run

    asked: list[str] = []
    scope = ManualScope(ManualAllowances(), lambda: "s", lambda: True)
    ask = payload_asker(tmp_path, scope, lambda q, a: asked.append(a) or False)
    sink = replace(sink_for_run(tmp_path, None, home=tmp_path / "home"), ask=ask)
    stored = sink.store(b"payload", ".bin")
    assert asked and asked[0].startswith("mcp_payload: mcp-output/")
    assert not (tmp_path / "mcp-output").exists(), "a declined payload was still written in the project"
    assert Path(stored.path).exists()  # it went to the app folder, so the call still answers


def test_an_unattended_mcp_payload_goes_to_the_app_folder_without_a_question(tmp_path: Path) -> None:
    from chimera.integrations.mcp_payloads import sink_for_run

    asked: list[str] = []
    scope = ManualScope(ManualAllowances(), lambda: "s", lambda: False)
    ask = payload_asker(tmp_path, scope, lambda q, a: asked.append(a) or True)
    sink = replace(sink_for_run(tmp_path, None, home=tmp_path / "home"), ask=ask)
    sink.store(b"payload", ".bin")
    assert asked == [] and not (tmp_path / "mcp-output").exists()


def test_an_allowed_mcp_payload_is_written_where_the_sink_says(tmp_path: Path) -> None:
    from chimera.integrations.mcp_payloads import sink_for_run

    scope = ManualScope(ManualAllowances(), lambda: "s", lambda: True)
    ask = payload_asker(tmp_path, scope, lambda q, a: True)
    sink = replace(sink_for_run(tmp_path, None, home=tmp_path / "home"), ask=ask)
    stored = sink.store(b"payload", ".bin")
    assert (tmp_path / "mcp-output").is_dir() and Path(stored.path).is_relative_to(tmp_path / "mcp-output")


# --- Deferral and sub-agents cannot go around the gate -------------------------------------------------


def test_a_deferred_writer_reached_through_tool_call_still_asks(tmp_path: Path) -> None:
    registry = _assembled(tmp_path, attended=False, CHIMERA_DEFER_TOOLS="1")
    assert "tool_call" in registry, "deferral did not run, so this proves nothing"
    proxy = registry.get("tool_call")
    while not hasattr(proxy, "catalogue"):
        proxy = getattr(proxy, "inner", None) or proxy._inner
    held = proxy.catalogue.by_name["download_media"]
    assert _is_gated(held), "the deferred download_media is not the gated one"
    out = str(registry.get("tool_call").run(
        tool="download_media", arguments={"url": "https://example.test/v", "out_dir": "dl"}))
    assert _NO_SCREEN in out, out
    assert not (tmp_path / "ws" / "dl").exists()


def test_a_subagent_gets_the_same_gated_writers(tmp_path: Path) -> None:
    from chimera.core.subagent import SubAgentTool

    registry = _assembled(tmp_path, attended=False)
    sub = SubAgentTool(None, lambda: registry)  # type: ignore[arg-type]
    inherited = sub._registry_of({"write_file", "edit_file"})
    assert set(inherited.names()) == {"write_file", "edit_file"}
    out = str(inherited.get("write_file").run(path="s.txt", content="x"))
    assert _NO_SCREEN in out, out
    assert not (tmp_path / "ws" / "s.txt").exists()


# --- Commands: exact key, long commands, spacing, and what only reads ---------------------------------


def test_a_command_allowance_is_the_exact_text() -> None:
    from chimera.api.code_api import _ask_command_on_card

    store = ManualAllowances()
    store.allow("s", command_key("echo a"))
    asked: list[str] = []
    ask = _ask_command_on_card(
        [lambda q, a: asked.append(a) or False], ManualScope(store, lambda: "s", lambda: True)
    )
    assert ask("echo a") is True
    variants = ["echo  a", "echo a ", " echo a", "echo a;", "ECHO a", "echo a\t"]
    for variant in variants:
        assert ask(variant) is False, variant
    assert len(asked) == len(variants)


def test_a_compound_command_asks_even_when_its_first_part_only_reads() -> None:
    from chimera.sandbox.confirm import _skip_what_only_reads

    asked: list[str] = []
    gate = _skip_what_only_reads(lambda command: asked.append(command) or False)
    assert gate("ls") is True and asked == []
    compound = ["ls; rm -rf x", "ls && rm x", "ls | tee out.txt", "ls > out.txt", "ls $(rm x)", "ls\nrm x"]
    for command in compound:
        assert gate(command) is False, command
    assert len(asked) == len(compound)


def test_a_very_long_command_is_asked_once_and_never_remembered() -> None:
    from chimera.api.code_api import _ask_command_on_card

    long_command = "echo " + "x" * (MAX_KEY_CHARS + 10)
    assert command_key(long_command) == ""
    seen: list[Any] = []
    ask = _ask_command_on_card(
        [lambda q, a: seen.append(q) or True], ManualScope(ManualAllowances(), lambda: "s", lambda: True)
    )
    assert ask(long_command) is True and seen[0].allow_key == ""
    store = ManualAllowances()
    assert store.allow("s", command_key(long_command)) is False
    assert not store.allowed("s", command_key(long_command))


def test_a_long_destination_is_asked_once_and_never_remembered(tmp_path: Path) -> None:
    asked: list[Any] = []
    stub = _Stub("write_file")
    scope = ManualScope(ManualAllowances(), lambda: "s", lambda: True)
    gate = ManualEditGate(stub, tmp_path, scope, lambda q, a: asked.append(q) or True,  # type: ignore[arg-type]
                          WORKSPACE_WRITERS["write_file"])
    gate.run(path="d/" + "x" * (MAX_KEY_CHARS + 5), content="c")
    assert asked[0].allow_key == ""


def test_two_destinations_in_one_call_offer_only_once(tmp_path: Path) -> None:
    asked: list[Any] = []
    stub = _Stub("edit_batch")
    scope = ManualScope(ManualAllowances(), lambda: "s", lambda: True)
    gate = ManualEditGate(stub, tmp_path, scope, lambda q, a: asked.append(q) or True,  # type: ignore[arg-type]
                          WORKSPACE_WRITERS["edit_batch"])
    gate.run(edits=[{"path": "a.txt", "old": "1", "new": "2"}, {"path": "b.txt", "old": "1", "new": "2"}])
    assert asked[0].allow_key == "" and "a.txt" in asked[0].action and "b.txt" in asked[0].action


def test_the_same_file_has_one_key_whatever_its_spelling(tmp_path: Path) -> None:
    asked: list[Any] = []
    stub = _Stub("write_file")
    scope = ManualScope(ManualAllowances(), lambda: "s", lambda: True)
    gate = ManualEditGate(stub, tmp_path, scope, lambda q, a: asked.append(q) or True,  # type: ignore[arg-type]
                          WORKSPACE_WRITERS["write_file"])
    for spelling in ("a.txt", "./a.txt", "sub/../a.txt"):
        gate.run(path=spelling, content="c")
    assert {q.allow_key for q in asked} == {"write_file:a.txt"}


# --- Manual's approver: a standing yes or an old rule does not answer for the owner -------------------------


def test_approval_mode_allow_does_not_answer_manual_for_the_owner(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, home = _app(tmp_path, monkeypatch, CHIMERA_APPROVAL_MODE="allow")
    _saved_conversation(app, "m2-1", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "x\n")]
    with _Owner(app, home, [{"approved": False}]) as owner:
        _turn(TestClient(app), "m2-1", project, MANUAL, **SCREEN)
    assert len(owner.seen) == 1, "CHIMERA_APPROVAL_MODE=allow answered Manual's question"
    assert not (project / "a.txt").exists()


def test_a_remembered_project_rule_does_not_answer_manual(tmp_path: Path, monkeypatch: Any) -> None:
    from chimera.governance.approval_rules import create_rule

    app, project, home = _app(tmp_path, monkeypatch)
    create_rule(home, project, "write_file", "a.txt")
    _saved_conversation(app, "m2-2", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "x\n")]
    with _Owner(app, home, [{"approved": False}]) as owner:
        _turn(TestClient(app), "m2-2", project, MANUAL, **SCREEN)
    assert len(owner.seen) == 1, "an old 'always allow' rule answered Manual's question"
    assert not (project / "a.txt").exists()


# --- The answer route: who may grant for a conversation, and what a failed answer leaves ---------------------


def _wait_for_question(home: Path) -> Any:
    from chimera.governance.pending import pending

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        rows = pending(home)
        if rows:
            return rows[0]
        time.sleep(0.02)
    raise AssertionError("no question appeared")


def _turn_in_thread(app: Any, session: str, project: Path) -> threading.Thread:
    thread = threading.Thread(
        target=lambda: _turn(TestClient(app), session, project, MANUAL, **SCREEN), daemon=True
    )
    thread.start()
    return thread


def test_the_bridge_cannot_grant_for_a_conversation_but_the_screen_can(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "m2-3", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "x\n")]
    thread = _turn_in_thread(app, "m2-3", project)
    question = _wait_for_question(home)
    body = {"approved": True, "remember": "conversation"}
    refused = _bridged(app).post(f"/api/approvals/{question.id}", json=body)
    assert refused.status_code == 403, refused.text
    bare = TestClient(app).post(f"/api/approvals/{question.id}", json=body)  # no screen credential
    assert bare.status_code == 403, bare.text
    assert not app.state.manual_allowances.allowed("m2-3", "write_file:a.txt")
    granted = TestClient(app).post(f"/api/approvals/{question.id}", json=body, headers=SCREEN)
    assert granted.status_code == 200 and granted.json()["outcome"] == "allowed_in_conversation"
    thread.join(20.0)
    assert app.state.manual_allowances.allowed("m2-3", "write_file:a.txt")
    assert (project / "a.txt").exists()


def test_an_allowance_is_undone_when_the_answer_does_not_land(tmp_path: Path, monkeypatch: Any) -> None:
    import chimera.governance.pending as pending_module

    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "m2-4", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "x\n")]
    thread = _turn_in_thread(app, "m2-4", project)
    question = _wait_for_question(home)
    real = pending_module.answer
    monkeypatch.setattr(pending_module, "answer", lambda *a, **k: False)
    stale = TestClient(app).post(
        f"/api/approvals/{question.id}", json={"approved": True, "remember": "conversation"}, headers=SCREEN
    )
    assert stale.json()["ok"] is False
    assert not app.state.manual_allowances.allowed("m2-4", "write_file:a.txt"), "a failed answer left a grant"
    monkeypatch.setattr(pending_module, "answer", real)
    TestClient(app).post(f"/api/approvals/{question.id}", json={"approved": False}, headers=SCREEN)
    thread.join(20.0)


def test_the_card_carries_the_scope_it_would_grant(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "m2-5", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "x\n")]
    thread = _turn_in_thread(app, "m2-5", project)
    question = _wait_for_question(home)
    listed = TestClient(app).get("/api/approvals", headers=SCREEN).json()
    assert [row["allow_scope"] for row in listed if row["id"] == question.id] == ["write_file:a.txt"]
    TestClient(app).post(f"/api/approvals/{question.id}", json={"approved": False}, headers=SCREEN)
    thread.join(20.0)


# --- Contract: the server derives the questions from the stored mode ----------------------------------------


@pytest.mark.parametrize("sent", [None, {"reach": "workspace", "approval": "suspicious"}, AUTO])
def test_a_manual_conversation_asks_even_when_the_body_leaves_edits_out(
    tmp_path: Path, monkeypatch: Any, sent: dict[str, str] | None
) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "m2-6", project, switch_on=False)
    client = TestClient(app)
    patched = client.patch("/api/code/sessions/m2-6/permission-mode", json={"mode": "manual"}, headers=SCREEN)
    assert patched.status_code == 200
    _ScriptedAgent.calls = [_write("a.txt", "x\n")]
    body: dict[str, Any] = {"message": "m", "session_id": "m2-6", "workspace": str(project), "stream": False}
    if sent is not None:
        body["posture"] = sent
    with _Owner(app, home, [{"approved": False}]) as owner:
        response = client.post("/api/code/turn", json=body, headers=SCREEN)
    assert response.status_code == 200, response.text
    assert len(owner.seen) == 1, "a body with no edits axis turned Manual's question off"
    assert not (project / "a.txt").exists()


def test_the_ignore_permissions_switch_still_lifts_manual(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "m2-7", project, switch_on=True)
    client = TestClient(app)
    client.patch("/api/code/sessions/m2-7/permission-mode", json={"mode": "manual"}, headers=SCREEN)
    _ScriptedAgent.calls = [_write("a.txt", "x\n")]
    _turn(client, "m2-7", project, AUTO, **SCREEN)
    assert _questions(home) == [] and (project / "a.txt").exists()
