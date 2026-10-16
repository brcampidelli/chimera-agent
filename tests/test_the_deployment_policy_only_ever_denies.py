"""The owner's deployment policy (``policy.json``) can only take things away, and holds on every path.

The golden rule, which is what separates this from a permissions file that grants: a later ``allow``
undoes an earlier ``deny`` of the SAME file and nothing else. It cannot reopen
``CHIMERA_TOOL_DENYLIST``, the per-surface reach floor, or a kernel block. Each test here would go
red if one of those promises broke:

- a denied provider leaves the model listings (no ``NameError`` on the remote and local merges, as
  the first draft had), the tier ladder never ends on it, and the gateway refuses a call to it — a
  denied primary falls to a permitted fallback;
- a denied MCP server is not listed and does not connect by any path — the pool, ``chimera mcp
  test``, the connect primitive itself — while its entry stays in ``mcp.json``;
- a denied skill is neither registered nor listed nor read;
- the agent's write tools cannot rewrite the policy, even when the workspace is the data folder or
  ``CHIMERA_POLICY_FILE`` points into the workspace;
- an invalid file stops ``serve``/``chat``/``tui``/``doctor``, a file that turns invalid at run time
  keeps the last valid rules, and the desktop reads the error on a status route;
- the bridge may not repoint the policy.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.config import Settings, get_settings
from chimera.deployment_policy import (
    PolicyError,
    load_policy,
    policy_error,
    policy_path,
    provider_of,
)

runner = CliRunner()


def _home() -> Path:
    return Path(get_settings().home)


def _write_policy(*rules: tuple[str, str, str], path: Path | None = None) -> Path:
    target = path or _home() / "policy.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {"rules": [{"action": a, "resource": r, "effect": e} for a, r, e in rules]}
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


# --- the rule itself ---------------------------------------------------------------------------


def test_a_later_allow_undoes_an_earlier_deny_of_the_same_file() -> None:
    _write_policy(
        ("provider.use", "*", "deny"),
        ("provider.use", "openrouter", "allow"),
        ("tool.use", "write_*", "deny"),
        ("tool.use", "write_file", "allow"),
    )
    policy = load_policy(get_settings())

    assert not policy.denied("provider.use", "openrouter")
    assert policy.denied("provider.use", "anthropic")
    assert not policy.denied("tool.use", "write_file")
    assert policy.denied("tool.use", "write_region")
    # No rule for this action: not denied, which is what no file at all has always meant.
    assert not policy.denied("integration.use", "mcp:anything")


def test_provider_rules_ignore_case_and_tool_rules_do_not() -> None:
    _write_policy(("provider.use", "OpenRouter", "deny"), ("tool.use", "Write_File", "deny"))
    policy = load_policy(get_settings())

    assert policy.denied("provider.use", "openrouter")
    assert policy.denies_provider_of("OPENROUTER/anthropic/claude-x")
    # Tool names are case-sensitive, as the registry is: a differently-cased rule denies nothing.
    assert not policy.denied("tool.use", "write_file")


@pytest.mark.parametrize(
    ("slug", "provider"),
    [
        ("openrouter/anthropic/claude-opus-5", "openrouter"),
        ("ollama_chat/llama3", "ollama"),
        ("ollama/llama3", "ollama"),
        ("Anthropic/claude-x", "anthropic"),
        ("gpt-4o", "openai"),
    ],
)
def test_a_slug_is_spoken_of_by_the_provider_that_receives_it(slug: str, provider: str) -> None:
    assert provider_of(slug) == provider


def test_the_path_is_read_from_settings_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_POLICY_FILE", str(tmp_path / "from-the-shell.json"))
    explicit = Settings(_env_file=None, CHIMERA_POLICY_FILE=str(tmp_path / "chosen.json"))  # type: ignore[call-arg]
    duck = SimpleNamespace(home=tmp_path / "home")

    assert policy_path(explicit) == tmp_path / "chosen.json"
    assert policy_path(duck) == tmp_path / "home" / "policy.json"


# --- providers ---------------------------------------------------------------------------------


def _option(slug: str, source: str) -> Any:
    from chimera.providers.listing import ModelOption

    return ModelOption(slug=slug, label=slug, vendor="v", source=source)  # type: ignore[arg-type]


def test_a_denied_provider_leaves_every_model_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import listing

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    remote = (_option("openrouter/x/a", "openrouter"),)
    monkeypatch.setattr(listing, "openrouter_models", lambda timeout_s=0: (remote, ""))
    monkeypatch.setattr(listing, "remember_models", lambda models: None)
    monkeypatch.setattr(
        listing, "_ollama_options", lambda base: [_option("ollama_chat/llama3", "ollama")]
    )

    before = listing.available_models(get_settings())
    assert any(m.slug == "ollama_chat/llama3" for m in before.models)
    assert any(m.slug.startswith("openrouter/") for m in before.models)

    _write_policy(("provider.use", "ollama", "deny"), ("provider.use", "openrouter", "deny"))
    after = listing.available_models(get_settings())

    assert not [m.slug for m in after.models if provider_of(m.slug) in {"ollama", "openrouter"}]
    assert "ollama" not in after.sources
    # The wizard asking what a denied provider's key buys gets nothing.
    assert listing.available_models(get_settings(), provider="openrouter").models == ()


def test_the_tier_ladder_never_ends_on_a_denied_provider() -> None:
    from chimera.providers.catalog import resolve_tiers

    def settings(**fields: str) -> Any:
        base = {
            "weak_model": "", "mid_model": "", "orchestrator_model": "", "cost_mode": "auto",
            "default_model": "anthropic/claude-x", "home": _home(), "policy_file": None,
        }
        base.update(fields)
        return SimpleNamespace(**base, configured_providers=lambda: ["openrouter", "anthropic"])

    _write_policy(("provider.use", "openrouter", "deny"))
    # The presets are all OpenRouter: every rung goes to the user's permitted default.
    preset = resolve_tiers(settings())
    assert set(preset.ladder()) == {"anthropic/claude-x"}
    # An explicit override naming a denied provider is the one typed choice that is replaced.
    typed = resolve_tiers(settings(orchestrator_model="openrouter/z/top"))
    assert all(provider_of(slug) != "openrouter" for slug in typed.ladder())

    _write_policy(("provider.use", "*", "deny"))
    from chimera.providers.gateway import ProviderDeniedError

    with pytest.raises(ProviderDeniedError):
        resolve_tiers(settings(orchestrator_model="openrouter/z/top"))


def _gateway(monkeypatch: pytest.MonkeyPatch, seen: list[str], **env: str) -> Any:
    import litellm

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    message = SimpleNamespace(content="ok", tool_calls=None)
    reply = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    def fake(**kwargs: Any) -> Any:
        seen.append(str(kwargs["model"]))
        return [] if kwargs.get("stream") else reply

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway

    return LLMGateway()


def test_the_gateway_refuses_a_denied_provider_and_falls_back_to_a_permitted_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chimera.providers.gateway import MissingCredentialsError, ProviderDeniedError

    seen: list[str] = []
    gateway = _gateway(monkeypatch, seen, CHIMERA_FALLBACK_MODELS="anthropic/claude-x")
    _write_policy(("provider.use", "openrouter", "deny"))

    result = gateway.complete([{"role": "user", "content": "hi"}], model="openrouter/a/b")
    assert result.content == "ok"
    assert seen == ["anthropic/claude-x"]
    assert [model for model, _ in gateway.planned_calls("openrouter/a/b")] == ["anthropic/claude-x"]

    # A single-shot path has no chain: it refuses, naming the provider, and never reaches LiteLLM.
    with pytest.raises(ProviderDeniedError, match="openrouter"):
        list(gateway.stream([{"role": "user", "content": "hi"}], model="openrouter/a/b"))

    _write_policy(("provider.use", "*", "deny"))
    with pytest.raises(MissingCredentialsError, match="deployment policy"):
        gateway.complete([{"role": "user", "content": "hi"}], model="openrouter/a/b")
    assert seen == ["anthropic/claude-x"]


# --- tools: the allow never reopens anything ---------------------------------------------------


def _registry(workspace: Path) -> Any:
    from chimera.tools.files import ReadFileTool, WriteFileTool
    from chimera.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace))
    return registry


def test_a_policy_allow_does_not_reopen_the_tool_denylist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.commands._shared import _apply_tool_allowlist

    monkeypatch.setenv("CHIMERA_TOOL_DENYLIST", "write_file")
    get_settings.cache_clear()
    _write_policy(("tool.use", "*", "deny"), ("tool.use", "write_file", "allow"))

    registry = _apply_tool_allowlist(
        _registry(tmp_path), allow=None, deny=None, settings=get_settings()
    )

    # read_file: denied by the policy. write_file: the policy's allow undid ITS deny, and the
    # denylist still removes it.
    assert registry.names() == []


def test_a_policy_allow_does_not_reopen_the_reach_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.posture import deployment_fence
    from chimera.governance import restrict_registry

    monkeypatch.setenv("CHIMERA_REACH", "read_only")
    get_settings.cache_clear()
    floor, _ = deployment_fence(get_settings())
    assert "write_file" in floor

    _write_policy(("tool.use", "write_file", "deny"), ("tool.use", "write_file", "allow"))
    assert deployment_fence(get_settings())[0] == floor
    kept = restrict_registry(_registry(tmp_path), allow=None, deny=sorted(floor))
    assert "write_file" not in kept.names()


def test_a_denied_tool_is_refused_wherever_the_registry_came_from(tmp_path: Path) -> None:
    from chimera.tools.base import is_refusal

    registry = _registry(tmp_path)
    _write_policy(("tool.use", "write_file", "deny"))

    out = registry.run("write_file", path="x.txt", content="hi")
    assert is_refusal(out)
    assert not (tmp_path / "x.txt").exists()
    names = [schema["function"]["name"] for schema in registry.to_openai_schema()]
    assert names == ["read_file"]


# --- integrations ------------------------------------------------------------------------------


def _store_server(name: str) -> Path:
    from chimera.integrations.mcp_config import McpServerConfig, save_servers

    path = _home() / "mcp.json"
    save_servers(path, [McpServerConfig(name=name, command="definitely-not-a-binary", args=[])])
    return path


def test_a_denied_mcp_server_does_not_connect_by_any_path() -> None:
    from chimera.cli.main import app
    from chimera.integrations import mcp_config
    from chimera.integrations.mcp_pool import _build

    path = _store_server("evil")
    _write_policy(("integration.use", "mcp:evil", "deny"))

    assert mcp_config.load_servers(path) == []
    assert _build(get_settings()) is None
    cfg = mcp_config._read_servers(path)[0]
    with pytest.raises(mcp_config.McpServerDeniedError):
        mcp_config.probe_tools(cfg, connect_timeout=0.1)
    result = runner.invoke(app, ["mcp", "test", "evil"])
    assert result.exit_code != 0
    assert "no MCP server named evil" in result.output

    # Hidden, not deleted: adding another server keeps the denied one in the store.
    mcp_config.add_server(path, mcp_config.McpServerConfig(name="ok", command="x", args=[]))
    assert {s.name for s in mcp_config._read_servers(path)} == {"evil", "ok"}


def test_a_denied_skill_is_not_loaded_listed_or_read() -> None:
    from chimera.skills.bundle_upload import read_skill_md
    from chimera.skills.bundles import bundles_root, installed
    from chimera.skills.registry import default_registry

    names = default_registry().names()
    assert names, "the built-in library is the thing this test denies one of"
    bundle = bundles_root(_home()) / "demo"
    bundle.mkdir(parents=True)
    (bundle / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    assert [b.name for b in installed(_home())] == ["demo"]

    _write_policy(("integration.use", f"skill:{names[0]}", "deny"), ("integration.use", "skill:demo", "deny"))

    assert names[0] not in default_registry().names()
    assert installed(_home()) == []
    assert read_skill_md("demo", _home()) is None


# --- the policy file is the owner's ------------------------------------------------------------


def _attempt(tool: Any, **kwargs: Any) -> str:
    """A write tool's answer: a refusal string, or the message of the guard it raised."""
    try:
        return str(tool.run(**kwargs))
    except Exception as exc:  # noqa: BLE001 — the workspace guards raise; both are refusals
        return f"error: {exc}"


def test_the_write_tools_cannot_rewrite_the_policy_when_the_workspace_is_home() -> None:
    from chimera.tools.edit import ApplyPatchTool, EditBatchTool, EditFileTool
    from chimera.tools.files import WriteFileTool

    home = _home()
    policy = _write_policy(("tool.use", "run_shell", "deny"))
    original = policy.read_text(encoding="utf-8")

    outputs = [
        _attempt(WriteFileTool(home), path="policy.json", content='{"rules": []}'),
        _attempt(EditFileTool(home), path="policy.json", old="deny", new="allow"),
        _attempt(
            ApplyPatchTool(home),
            path="policy.json",
            patch="<<<<<<< SEARCH\ndeny\n=======\nallow\n>>>>>>> REPLACE",
        ),
        _attempt(EditBatchTool(home), edits=[{"path": "policy.json", "old": "deny", "new": "allow"}]),
    ]
    assert all("error" in out.lower() or "refus" in out.lower() for out in outputs), outputs
    assert policy.read_text(encoding="utf-8") == original


def test_the_write_tools_cannot_rewrite_a_policy_file_named_by_the_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.tools.edit import EditFileTool
    from chimera.tools.files import WriteFileTool

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = _write_policy(("tool.use", "run_shell", "deny"), path=workspace / "owner-policy.json")
    monkeypatch.setenv("CHIMERA_POLICY_FILE", str(policy))
    get_settings.cache_clear()
    original = policy.read_text(encoding="utf-8")

    assert "policy" in _attempt(WriteFileTool(workspace), path="owner-policy.json", content="{}")
    assert "policy" in _attempt(
        EditFileTool(workspace), path="owner-policy.json", old="deny", new="allow"
    )
    assert policy.read_text(encoding="utf-8") == original
    # Ordinary files beside it are still ordinary work.
    assert "error" not in _attempt(WriteFileTool(workspace), path="notes.txt", content="fine")


# --- an invalid file ---------------------------------------------------------------------------


def _break_policy() -> Path:
    path = _home() / "policy.json"
    path.write_text('{"rules": [{"action": "network.open", "resource": "*", "effect": "deny"}]}', encoding="utf-8")
    return path


def test_an_invalid_file_is_refused_whole() -> None:
    _break_policy()
    with pytest.raises(PolicyError, match="Invalid deployment policy"):
        load_policy(get_settings())
    assert policy_error(get_settings()) is not None


@pytest.mark.parametrize("command", ["serve", "chat", "tui", "doctor"])
def test_an_invalid_file_stops_the_terminal_surfaces(command: str) -> None:
    from chimera.cli.main import app

    _break_policy()
    result = runner.invoke(app, [command])

    assert result.exit_code != 0
    assert "deployment policy" in result.output.replace("\n", " ")


def test_policy_show_says_when_there_is_no_file() -> None:
    from chimera.cli.main import app

    result = runner.invoke(app, ["policy", "show"])
    assert result.exit_code == 0
    assert "no policy file: everything allowed" in result.output


def test_a_file_that_turns_invalid_at_run_time_keeps_the_last_valid_rules(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from chimera.deployment_policy import tool_denied

    _write_policy(("tool.use", "run_shell", "deny"))
    assert tool_denied("run_shell")

    _break_policy()
    with caplog.at_level(logging.ERROR, logger="chimera.policy"):
        assert load_policy(get_settings()).denied("tool.use", "run_shell")
    assert tool_denied("run_shell")
    assert not tool_denied("read_file")
    assert "keeping the last valid policy" in caplog.text

    (_home() / "policy.json").unlink()
    with caplog.at_level(logging.WARNING, logger="chimera.policy"):
        assert not load_policy(get_settings()).denied("tool.use", "run_shell")
    assert "removed" in caplog.text


def test_an_invalid_file_never_seen_valid_refuses_every_tool() -> None:
    from chimera.deployment_policy import integration_denied, tool_denied

    _break_policy()
    assert tool_denied("read_file")
    assert integration_denied("skill:anything")


def test_the_desktop_reads_the_error_on_a_status_route(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.interface import ChatSession
    from tests.test_api import _FakeAgent  # noqa: PLC0415

    _break_policy()
    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=get_settings()))
    body = client.get("/api/governance/policy").json()
    assert body["exists"] is True
    assert body["in_force"] is False
    assert "Invalid deployment policy" in body["error"]


# --- the bridge --------------------------------------------------------------------------------


def test_the_bridge_may_not_repoint_the_policy(tmp_path: Path) -> None:
    """Not a setting any screen writes, so the one write path the bridge has refuses it outright."""
    from chimera.api.bridge_routes import SUGGESTABLE_SETTINGS
    from chimera.api.config_api import is_editable, patch_config

    env = tmp_path / ".env"
    assert not is_editable("CHIMERA_POLICY_FILE")
    assert "CHIMERA_POLICY_FILE" not in SUGGESTABLE_SETTINGS
    with pytest.raises(ValueError, match="CHIMERA_POLICY_FILE"):
        patch_config({"CHIMERA_POLICY_FILE": str(tmp_path / "open.json")}, env_path=env)
    assert not env.exists() or "CHIMERA_POLICY_FILE" not in env.read_text(encoding="utf-8")
