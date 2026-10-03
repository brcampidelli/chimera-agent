"""The two deferral switches are on the Settings screen, off, with the saving measured beside them.

`CHIMERA_DEFER_TOOLS` and `CHIMERA_MCP_DEFER` were reachable only through `.env`, and `config.py`
promised beside both that `describe_saving` "reports" the saving on your own installation — while
nothing outside the tests called either `describe_saving`. So the tests hold four things:

* the screen can read and write both, and nothing about that turned either one on;
* the saving is measured on THIS install by a route and a command, can be a LOSS, and the route
  never connects an MCP server to measure one;
* turning the MCP switch on from the screen cannot open the hole it opened on the two chat surfaces:
  they registered the proxy with no lists, so a denylisted server tool stayed reachable via
  `mcp_call`;
* with both switches on, the MCP proxies are not themselves deferred behind `tool_list`.

Nothing here spawns a server or calls a model.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chimera.api.config_api import APPLIES_WHEN, is_editable, patch_config, read_config
from chimera.api.schemas import ConfigOut, DeferCfgOut
from chimera.config import Settings, get_settings
from chimera.integrations import mcp_pool
from chimera.integrations.mcp_defer import mount
from chimera.tools.base import Tool
from chimera.tools.defer import defer_builtins
from chimera.tools.defer import describe_saving as builtin_saving
from chimera.tools.registry import ToolRegistry

SWITCHES = {"CHIMERA_DEFER_TOOLS": "tools", "CHIMERA_MCP_DEFER": "mcp"}


class _ServerTool(Tool):
    """A server-published tool with a schema long enough that deferring it is a real saving."""

    untrusted_output = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "Open an issue in a repository, with a title and a body."
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/name of the repository."},
                "title": {"type": "string", "description": "One line, under eighty characters."},
                "body": {"type": "string", "description": "Markdown. May be long."},
            },
            "required": ["repo", "title"],
        }
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return f"{self.name} ran"


class _Pool:
    def __init__(self, *names: str) -> None:
        self.tools = [_ServerTool(n) for n in names]

    def into_tool_registry(self, registry: ToolRegistry) -> None:
        for tool in self.tools:
            registry.register(tool)

    def all_tools(self) -> list[Tool]:
        return list(self.tools)

    def names(self) -> list[str]:
        return ["github"]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in (
        "CHIMERA_MCP_AUTOLOAD",
        "CHIMERA_MCP_DEFER",
        "CHIMERA_DEFER_TOOLS",
        "CHIMERA_TOOL_DENYLIST",
        "CHIMERA_TOOL_ALLOWLIST",
        "CHIMERA_REACH",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    mcp_pool.reset_for_tests()
    yield
    mcp_pool.reset_for_tests()
    get_settings.cache_clear()


def _settings(tmp_path: Path, **env: str) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path / "home"), **env)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the switches


@pytest.mark.parametrize("env", sorted(SWITCHES))
def test_the_screen_may_write_each_switch(env: str) -> None:
    assert is_editable(env)


def test_both_are_off_when_nobody_chose_them(tmp_path: Path) -> None:
    assert read_config(_settings(tmp_path))["defer"] == {"tools": False, "mcp": False}
    # A server that predates the block reads as both off, not as a validation error.
    assert "defer" in ConfigOut.model_fields
    assert DeferCfgOut().model_dump() == {"tools": False, "mcp": False}


@pytest.mark.parametrize(("env", "field"), sorted(SWITCHES.items()))
def test_the_screen_reads_each_one_back_alone(env: str, field: str, tmp_path: Path) -> None:
    reported = read_config(_settings(tmp_path, **{env: "true"}))["defer"]
    assert [k for k, v in reported.items() if v] == [field]


@pytest.mark.parametrize(("env", "field"), sorted(SWITCHES.items()))
def test_a_save_reaches_the_reader(
    env: str, field: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(env, "false")  # owned first, so the teardown restores os.environ
    get_settings.cache_clear()
    env_file = tmp_path / ".env"

    patch_config({env: "true"}, env_path=env_file)

    assert env_file.read_text(encoding="utf-8").splitlines() == [f"{env}=true"]
    assert read_config(get_settings())["defer"][field] is True


@pytest.mark.parametrize("env", sorted(SWITCHES))
def test_a_value_that_is_not_a_boolean_is_refused_before_it_is_written(
    env: str, tmp_path: Path
) -> None:
    """Saved, it would make `Settings` fail to build and take the app down at the next read."""
    env_file = tmp_path / ".env"
    with pytest.raises(ValueError, match="true or false"):
        patch_config({env: "maybe"}, env_path=env_file)
    assert not env_file.exists()


@pytest.mark.parametrize("env", sorted(SWITCHES))
def test_each_says_it_waits_for_the_next_conversation(env: str) -> None:
    """The registry is assembled per conversation in the chat; an open one keeps its tool list."""
    assert APPLIES_WHEN[env] == "next_conversation"


# --------------------------------------------------------------------------- the measured saving


def _client(tmp_path: Path, **env: str) -> TestClient:
    from chimera.api.app import build_api_app

    return TestClient(
        build_api_app(  # type: ignore[arg-type]
            lambda: None, settings=_settings(tmp_path, **env), workspace=tmp_path
        )
    )


def test_the_route_reports_the_builtin_half_of_the_real_registry(tmp_path: Path) -> None:
    from chimera.tools.builtin import default_registry

    body = _client(tmp_path).get("/api/tools/defer-saving").json()

    expected = builtin_saving(default_registry(tmp_path))
    assert body["builtin"]["declared_chars"] == expected["declared_chars"]
    assert body["builtin"]["deferred_chars"] == expected["deferred_chars"]
    assert body["builtin"]["deferred"] == expected["deferred"]
    # On the stock registry the core is a small part of the schema, so this one is a saving.
    assert body["builtin"]["saving_pct"] > 0


def test_a_denied_tool_is_not_counted_as_a_saving(tmp_path: Path) -> None:
    """The denylist already took it out of both shapes; crediting deferral with it inflates it."""
    whole = _client(tmp_path).get("/api/tools/defer-saving").json()["builtin"]
    fenced = (
        _client(tmp_path, CHIMERA_TOOL_DENYLIST="scrape")
        .get("/api/tools/defer-saving")
        .json()["builtin"]
    )
    assert fenced["tools"] == whole["tools"] - 1
    assert fenced["declared_chars"] < whole["declared_chars"]


def test_with_autoload_off_there_is_no_mcp_figure_and_it_says_why(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/tools/defer-saving").json()
    assert body["mcp"] is None
    assert body["mcp_state"] == "autoload_off"


def test_the_route_never_connects_a_server_to_measure_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A status read that spawns a subprocess per configured server is the failure `PoolState`
    was written to prevent. Not built yet is reported as such, and nothing is built."""
    built: list[Any] = []
    monkeypatch.setattr(mcp_pool, "_build", lambda s: built.append(s) or _Pool("gh_x"))

    body = _client(tmp_path, CHIMERA_MCP_AUTOLOAD="1").get("/api/tools/defer-saving").json()

    assert built == []
    assert body["mcp"] is None
    assert body["mcp_state"] == "not_connected"


def test_once_a_conversation_connected_them_the_servers_are_measured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pool = _Pool(*(f"github_tool_{i}" for i in range(12)))
    monkeypatch.setattr(mcp_pool, "_build", lambda s: pool)
    mcp_pool.connectors(_settings(tmp_path, CHIMERA_MCP_AUTOLOAD="1"))  # what a turn does

    body = _client(tmp_path, CHIMERA_MCP_AUTOLOAD="1").get("/api/tools/defer-saving").json()

    assert body["mcp_state"] == "measured"
    assert body["mcp"]["tools"] == 12
    assert body["mcp"]["deferred"] == 12
    assert body["mcp"]["saving_pct"] > 0


def test_deferring_one_small_server_is_reported_as_the_loss_it_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Three proxies replacing one tool cost more than the tool. The number says so, negative,
    rather than being clamped to zero or hidden."""
    monkeypatch.setattr(mcp_pool, "_build", lambda s: _Pool("github_one"))
    mcp_pool.connectors(_settings(tmp_path, CHIMERA_MCP_AUTOLOAD="1"))

    mcp = _client(tmp_path, CHIMERA_MCP_AUTOLOAD="1").get("/api/tools/defer-saving").json()["mcp"]

    assert mcp["deferred_chars"] > mcp["declared_chars"]
    assert mcp["saving_pct"] < 0


def test_a_pool_that_came_back_empty_is_not_reported_as_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mcp_pool, "_build", lambda s: None)
    mcp_pool.connectors(_settings(tmp_path, CHIMERA_MCP_AUTOLOAD="1"))

    body = _client(tmp_path, CHIMERA_MCP_AUTOLOAD="1").get("/api/tools/defer-saving").json()

    assert body["mcp_state"] == "no_servers"


def test_the_command_reports_the_saving_for_a_machine_with_no_screen(tmp_path: Path) -> None:
    from chimera.cli.main import app

    result = CliRunner().invoke(app, ["tools", "--defer-saving", "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "built-in:" in result.output
    assert "CHIMERA_DEFER_TOOLS=off" in result.output
    assert "MCP: not measured" in result.output
    # The quality half is said every time, with the bench's own number.
    assert "p = 0.125" in result.output


def test_the_command_does_not_connect_servers_while_autoload_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.cli.main import app

    built: list[Any] = []
    monkeypatch.setattr(mcp_pool, "_build", lambda s: built.append(s) or _Pool("gh_x"))

    CliRunner().invoke(app, ["tools", "--defer-saving", "--workspace", str(tmp_path)])

    assert built == []


# --------------------------------------------------------------------------- what the switch opens


def _reach(registry: ToolRegistry, name: str) -> str:
    """What a run gets when it asks the MCP proxy for ``name``. No proxy at all is a refusal too."""
    if "mcp_call" not in registry.names():
        return "no proxy"
    return str(registry.run("mcp_call", tool=name, arguments={"repo": "a/b", "title": "x"}))


def test_a_denylisted_server_tool_is_refused_through_the_chat_proxy(tmp_path: Path) -> None:
    """The hole the switch would have opened: the chat surfaces handed the proxy no lists, and the
    fence after them cannot match names that are no longer in the registry."""
    pool = _Pool("github_create_issue", "github_delete_repo")
    registry = ToolRegistry()

    mount(
        pool,
        registry,
        _settings(tmp_path, CHIMERA_MCP_DEFER="1", CHIMERA_TOOL_DENYLIST="github_delete_repo"),
    )

    assert "error: no MCP tool named" in _reach(registry, "github_delete_repo")
    assert pool.tools[1].calls == []
    assert "github_delete_repo" not in registry.run("mcp_list")
    # The permitted one is still reached: the fence narrows, it does not switch the proxy off.
    assert _reach(registry, "github_create_issue") == "github_create_issue ran"


def test_an_allowlist_bounds_the_chat_proxy_too(tmp_path: Path) -> None:
    pool = _Pool("github_create_issue", "github_delete_repo")
    registry = ToolRegistry()

    mount(
        pool,
        registry,
        _settings(
            tmp_path,
            CHIMERA_MCP_DEFER="1",
            CHIMERA_TOOL_ALLOWLIST="read_file,github_create_issue",
        ),
    )

    assert "error: no MCP tool named" in _reach(registry, "github_delete_repo")
    assert pool.tools[1].calls == []


def test_declared_mcp_tools_are_unchanged_when_the_switch_is_off(tmp_path: Path) -> None:
    pool = _Pool("github_create_issue")
    registry = ToolRegistry()

    mount(pool, registry, _settings(tmp_path))

    assert registry.names() == ["github_create_issue"]


def test_the_terminal_refuses_a_denied_server_tool_with_deferral_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end on a real chat assembly (`build_right_hand`), not only on the helper."""
    from chimera.cli.right_hand import build_right_hand

    pool = _Pool("github_create_issue", "github_delete_repo")
    monkeypatch.setattr("chimera.integrations.mcp_pool.connectors", lambda _s: pool)
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "1")
    monkeypatch.setenv("CHIMERA_MCP_DEFER", "1")
    monkeypatch.setenv("CHIMERA_TOOL_DENYLIST", "github_delete_repo")
    get_settings.cache_clear()

    hand = build_right_hand(tmp_path, settings=get_settings(), surface="chat")

    listed = hand.registry.run("mcp_list")
    assert "github_create_issue" in listed
    assert "github_delete_repo" not in listed
    assert "error: no MCP tool named" in _reach(hand.registry, "github_delete_repo")
    assert pool.tools[1].calls == []


def test_with_both_switches_on_the_mcp_proxies_stay_declared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The chat surfaces mount MCP before the built-in deferral runs; without the rule the three
    MCP proxies were swept behind `tool_list` — a round trip to reach a round trip."""
    from chimera.cli.right_hand import build_right_hand

    monkeypatch.setattr(
        "chimera.integrations.mcp_pool.connectors", lambda _s: _Pool("github_create_issue")
    )
    for var in ("CHIMERA_MCP_AUTOLOAD", "CHIMERA_MCP_DEFER", "CHIMERA_DEFER_TOOLS"):
        monkeypatch.setenv(var, "1")
    get_settings.cache_clear()

    names = set(build_right_hand(tmp_path, settings=get_settings(), surface="chat").registry.names())

    assert {"mcp_list", "mcp_describe", "mcp_call"} <= names
    assert "tool_list" in names  # the built-in deferral did run


def test_the_builtin_deferral_never_takes_an_mcp_proxy() -> None:
    registry = ToolRegistry()
    for name in ("read_file", "mcp_list", "mcp_describe", "mcp_call", "scrape"):
        registry.register(_ServerTool(name))

    deferred, count = defer_builtins(registry)

    assert count == 1  # scrape only
    assert {"mcp_list", "mcp_describe", "mcp_call"} <= set(deferred.names())
    assert builtin_saving(registry)["deferred"] == 1
