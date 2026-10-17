"""The project's Markdown commands and subagents reach the desktop app (plugin plan, step B7).

Before this, ``.chimera/commands/*.md`` expanded only in the terminal chat and the TUI and
``.chimera/agents/*.md`` ran only behind ``chimera solve --subagents``: the app had no route to
either, so a person who wrote one in their repository could not see it from the screen they use.

What is pinned here is that the app reaches them by the terminal's rules and no further: the same
discovery and precedence, the same expansion (nothing executes), the same taint for a plugin's
command, the app's own names kept, and an agents list that is read only and says honestly that the
Code turn cannot run them yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.steplog import StepLog
from chimera.interface import ChatSession
from chimera.plugins import registry
from tests.plugin_trees import full_plugin


class _EchoAgent:
    """Records the task it was handed, answers once, calls no tool."""

    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    def run(self, task: str, **_: Any) -> AgentResult:
        self.seen.append(task)
        return AgentResult(
            answer="ok",
            steps=1,
            stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
            model="test/model",
            steplog=StepLog(),
        )


def _command(root: Path, name: str, text: str) -> None:
    path = root / ".chimera" / "commands" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _owner_command(home: Path, name: str, text: str) -> None:
    path = home / "commands" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seen: list[str] | None = None
) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    record = seen if seen is not None else []
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _EchoAgent(record), raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(
        build_api_app(lambda: ChatSession(_EchoAgent(record)), workspace=ws, settings=settings)
    )


def _frames(response: Any) -> dict[str, dict[str, Any]]:
    event, out = "", {}
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


# --- the commands listing ------------------------------------------------------------------------


def test_the_listing_is_per_workspace_and_the_project_wins_a_shared_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    one, two = tmp_path / "one", tmp_path / "two"
    _command(one, "review", "---\ndescription: Review here\nargument-hint: <path>\n---\nReview $1")
    _command(two, "ship", "Ship it")
    _owner_command(home, "review", "---\ndescription: The owner's review\n---\nOwner review")
    _owner_command(home, "standup", "---\ndescription: Daily notes\n---\nWrite my standup")

    first = client.get("/api/code/commands", params={"workspace": str(one)}).json()["commands"]
    second = client.get("/api/code/commands", params={"workspace": str(two)}).json()["commands"]

    by_name = {c["name"]: c for c in first}
    assert by_name["review"] == {
        "name": "review",
        "description": "Review here",
        "source": "project",
        "argument_hint": "<path>",
        "error": None,
    }
    assert by_name["standup"]["source"] == "owner"
    assert "ship" not in by_name
    names = {c["name"]: c["source"] for c in second}
    # The other folder sees its own file, and the owner's review because nothing shadows it there.
    assert names == {"review": "owner", "ship": "project", "standup": "owner"}


def test_a_folder_that_does_not_exist_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _app(tmp_path, monkeypatch)
    got = client.get("/api/code/commands", params={"workspace": str(tmp_path / "gone")})
    assert got.status_code == 400


def test_a_plugin_command_is_listed_only_while_the_plugin_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    registry.install(str(full_plugin(tmp_path / "src")), home)

    def names() -> dict[str, dict[str, Any]]:
        listed = client.get("/api/code/commands").json()["commands"]
        return {c["name"]: c for c in listed}

    assert "my-plugin:hello" not in names()  # installed, not enabled
    registry.enable(home, "my-plugin")
    hello = names()["my-plugin:hello"]
    assert hello["source"] == "plugin:my-plugin"
    assert hello["argument_hint"] == "<who>"
    assert hello["description"] == "Say hello"
    registry.disable(home, "my-plugin")
    assert "my-plugin:hello" not in names()


def test_a_reserved_name_is_neither_listed_nor_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/undo` undoes in every conversational surface; a project file must not turn it into text."""
    from chimera.api.commands_api import expand_turn_message, reserved_command_names

    client = _app(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    _command(ws, "undo", "Pretend to undo")
    _command(ws, "fine", "A real one")

    body = client.get("/api/code/commands").json()
    assert "undo" in body["reserved"] and "undo" in reserved_command_names()
    assert [c["name"] for c in body["commands"]] == ["fine"]
    assert expand_turn_message("/undo", ws, tmp_path / "home") is None
    assert expand_turn_message("/fine", ws, tmp_path / "home") is not None


# --- expansion -----------------------------------------------------------------------------------


def test_expansion_follows_the_terminals_rules(tmp_path: Path) -> None:
    from chimera.api.commands_api import expand_turn_message

    ws, home = tmp_path / "ws", tmp_path / "home"
    _command(ws, "pair", "First $1, rest $2")
    _command(ws, "all", "Everything: $ARGUMENTS")
    _command(ws, "plain", "No placeholders")
    _command(ws, "nested", "Then run /pair a b and `rm -rf /` $ARGUMENTS")
    _command(ws, "broken", "---\nunknown: field\n---\nbody")

    def text(message: str) -> str | None:
        got = expand_turn_message(message, ws, home)
        return None if got is None else got.text

    assert text('/pair "one two" three four') == "First one two, rest three four"
    assert text("/all a  b") == "Everything: a  b"
    assert text("/plain extra") == "No placeholders\n\nextra"
    # A slash command inside a template is text: nothing re-expands, nothing runs.
    assert text("/nested now") == "Then run /pair a b and `rm -rf /` now"
    assert text("plain words") is None
    assert text("/nobody here") is None
    assert text("/pairs x") is None  # a word boundary, not a prefix
    with pytest.raises(ValueError, match="invalid frontmatter"):
        expand_turn_message("/broken", ws, home)


def test_a_turn_sends_the_expanded_text_and_the_receipt_names_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    client = _app(tmp_path, monkeypatch, seen)
    _command(tmp_path / "ws", "review", "Review the file $1 carefully")

    response = client.post("/api/code/turn", json={"message": "/review app.py"})

    assert response.status_code == 200, response.text
    assert seen[-1].endswith("Review the file app.py carefully")
    done = _frames(response)["done"]
    assert done["command"] == {"name": "review", "source": "project"}
    assert done["tainted"] is False  # the project's own file is not somebody else's text
    session_id = _frames(response)["session"]["session_id"]
    receipt = client.get(f"/api/code/sessions/{session_id}").json()["exchanges"][-1]["done"]
    assert receipt["command"] == {"name": "review", "source": "project"}


def test_a_plain_turn_carries_no_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    client = _app(tmp_path, monkeypatch, seen)
    done = _frames(client.post("/api/code/turn", json={"message": "/nothing by that name"}))["done"]
    assert "command" not in done
    assert seen[-1].endswith("/nothing by that name")


def test_a_broken_command_refuses_the_turn_instead_of_sending_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    client = _app(tmp_path, monkeypatch, seen)
    _command(tmp_path / "ws", "broken", "---\nunknown: field\n---\nbody")
    got = client.post("/api/code/turn", json={"message": "/broken"})
    assert got.status_code == 422
    assert "command unavailable" in got.text
    assert seen == []


def test_a_plugin_command_marks_the_turn_tainted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    client = _app(tmp_path, monkeypatch, seen)
    home = tmp_path / "home"
    registry.install(str(full_plugin(tmp_path / "src")), home)
    registry.enable(home, "my-plugin")

    done = _frames(client.post("/api/code/turn", json={"message": "/my-plugin:hello Ana"}))["done"]

    assert done["command"] == {"name": "my-plugin:hello", "source": "plugin:my-plugin"}
    assert done["tainted"] is True
    # Fenced and attributed, exactly as the terminal sends it.
    assert "comes from an installed plugin" in seen[-1] and "Say hello to Ana." in seen[-1]


def test_the_taint_guard_is_not_inert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the ledger call disabled, the same plugin turn reads clean — so the assertion above is
    measuring the call, not something else that happens to taint the turn."""
    import chimera.api.code_api as code_api

    monkeypatch.setattr(code_api, "taint_for_plugin_command", lambda *_a, **_k: False)
    client = _app(tmp_path, monkeypatch)
    home = tmp_path / "home"
    registry.install(str(full_plugin(tmp_path / "src")), home)
    registry.enable(home, "my-plugin")
    done = _frames(client.post("/api/code/turn", json={"message": "/my-plugin:hello Ana"}))["done"]
    assert done["command"]["name"] == "my-plugin:hello"
    assert done["tainted"] is False


def test_the_app_and_the_terminal_record_a_plugin_command_by_the_same_function() -> None:
    root = Path(__file__).resolve().parents[1]
    hand = (root / "chimera" / "cli" / "right_hand.py").read_text(encoding="utf-8")
    code = (root / "chimera" / "api" / "code_api.py").read_text(encoding="utf-8")
    assert "return taint_for_plugin_command(self.ledger, command, text)" in hand
    assert "taint_for_plugin_command(ledger, req.expanded_command, req.message)" in code


def test_a_command_cannot_be_declared_by_the_request_body() -> None:
    """The expanded command is a private attribute: a body naming it changes nothing."""
    from chimera.api.code_api import CodeTurnRequest
    from chimera.core.commands import PromptCommand

    req = CodeTurnRequest.model_validate({"message": "hi", "_command": "x", "command": "y"})
    assert req.expanded_command is None
    assert "_command" not in CodeTurnRequest.model_json_schema()["properties"]
    plugin = PromptCommand("p:c", "", "plugin:p", "T", untrusted=True)
    expanded = CodeTurnRequest(message="/p:c now").with_command("T now", plugin)
    assert expanded.expanded_command is plugin
    assert expanded.message == "T now"
    assert expanded.shown_message == "/p:c now"  # the title, the running row and memory read this
    assert expanded.model_copy(update={"model": "m"}).expanded_command is plugin
    assert "_command" not in expanded.model_dump()


# --- the agents listing --------------------------------------------------------------------------


def _agent(folder: Path, name: str, text: str) -> None:
    path = folder / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_agents_listing_shows_sources_models_effective_tools_and_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.providers.catalog import CATALOG

    client = _app(tmp_path, monkeypatch)
    home, ws = tmp_path / "home", tmp_path / "ws"
    slug = CATALOG[0].slug
    _agent(
        home / "agents",
        "planner",
        f"---\ndescription: Plans\nmodel: {slug}\ntools: [read_file, not_a_tool]\nsteps: 4\n---\nPlan.",
    )
    _agent(
        home / "agents",
        "shared",
        "---\ndescription: Owner's\ntools: [read_file]\nsteps: 2\n---\nO.",
    )
    _agent(
        ws / ".chimera" / "agents",
        "shared",
        f"---\ndescription: Project's\nmodel: {slug}\ntools: [read_file, write_file]\nsteps: 3\n---\nP.",
    )
    _agent(ws / ".chimera" / "agents", "bad", "no frontmatter at all")
    registry.install(str(full_plugin(tmp_path / "src")), home)
    registry.enable(home, "my-plugin")

    body = client.get("/api/agents/markdown").json()

    assert body["runs_in_code"] is False
    agents = {a["id"]: a for a in body["agents"]}
    planner = agents["planner"]
    assert (planner["source"], planner["model"], planner["model_ignored"]) == ("owner", slug, False)
    assert planner["declared_tools"] == ["read_file", "not_a_tool"]
    assert planner["effective_tools"] == ["read_file"]  # an unknown name is not a grant
    shared = agents["shared"]
    assert (shared["source"], shared["model"], shared["model_ignored"]) == ("project", None, True)
    assert shared["description"] == "Project's"
    reviewer = agents["my-plugin:reviewer"]
    assert reviewer["source"] == "plugin:my-plugin" and reviewer["model"] is None
    assert reviewer["model_ignored"] is True
    assert set(reviewer["effective_tools"]) <= set(reviewer["declared_tools"])
    messages = {(i["agent_id"], i["message"]) for i in body["issues"]}
    assert ("shared", "shadowed by project agent with the same name") in messages
    assert any(agent_id == "bad" for agent_id, _ in messages)
    assert any(
        m.startswith("model is chosen by the owner")
        for agent_id, m in messages
        if agent_id == "shared"
    )


def test_the_owners_floor_narrows_the_effective_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.commands_api import list_markdown_agents

    home, ws = tmp_path / "home", tmp_path / "ws"
    ws.mkdir()
    _agent(
        home / "agents",
        "writer",
        "---\ndescription: Writes\ntools: [read_file, write_file]\nsteps: 2\n---\nWrite.",
    )
    open_ = list_markdown_agents(ws, Settings(CHIMERA_HOME=str(home)))
    fenced = list_markdown_agents(
        ws, Settings(CHIMERA_HOME=str(home), CHIMERA_TOOL_DENYLIST="write_file")
    )
    assert open_["agents"][0]["effective_tools"] == ["read_file", "write_file"]
    assert fenced["agents"][0]["effective_tools"] == ["read_file"]


def test_the_new_listings_are_operate_routes_on_the_bridge() -> None:
    from chimera.api.bridge_routes import ROUTES

    assert ROUTES["projects.commands"].path == "/api/code/commands"
    assert ROUTES["agents.markdown"].path == "/api/agents/markdown"
    assert ROUTES["projects.commands"].tier == "operate" == ROUTES["agents.markdown"].tier
    assert ROUTES["projects.commands"].method == "GET" == ROUTES["agents.markdown"].method
