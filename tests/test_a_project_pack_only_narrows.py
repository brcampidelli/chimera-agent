"""A project's pack narrows what the owner switched on, and nothing a pack names can widen it.

Study 29, P7.6. Skills and MCP servers are per home, so the servers and skills one product needs
ride into every conversation. `.chimera/pack.json` lets a folder keep only some of them — and since
the file can arrive in a clone, the property worth testing is not that it narrows but that it can do
nothing else:

* a bundle the pack names that is pending, off or absent stays out — it is not activated;
* a server the pack names that is not configured adds no tool — there is nothing to launch it with;
* `tools_deny` joins the owner's denials and cannot undo one; no key turns into an allowlist;
* every key a pack cannot set (`tools_allow`, `reach`, `verify`, a server definition) is reported
  as ignored instead of being read;
* nothing applies with `CHIMERA_PROJECT_PACK` off (the default), nor before the owner accepted
  these exact bytes for this folder; after the file changes, the version the owner accepted keeps
  applying until they accept the new one or revoke (the review's tamper finding — see
  `test_a_pack_that_cannot_be_trusted_keeps_the_owners_narrowing.py`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.core import project_pack as packs
from chimera.integrations import mcp_pool
from chimera.skills import bundles

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_pool():
    mcp_pool.reset_for_tests()
    yield
    mcp_pool.reset_for_tests()


def _bundle(home: Path, name: str, status: str = "active") -> None:
    root = bundles.bundles_root(home) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    (root / "bundle.json").write_text(
        # An "active" one as `set_status` records an owner's switch: with the time it was thrown.
        json.dumps(
            {
                "name": name,
                "description": f"The {name} skill.",
                "status": status,
                **({"switched_on_at": "2026-10-03T00:00:00+00:00"} if status == "active" else {}),
            }
        ),
        encoding="utf-8",
    )


def _pack(folder: Path, body: Any) -> str:
    path = folder / packs.PACK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
    path.write_bytes(raw)
    found = packs.read_pack(folder)
    return found.digest


def _settings(home: Path, *, on: bool = True) -> Settings:
    return Settings(CHIMERA_HOME=str(home), CHIMERA_PROJECT_PACK=on)  # type: ignore[call-arg]


# --- the switch and the consent -----------------------------------------------------------------


def test_the_switch_ships_off() -> None:
    assert Settings.model_fields["project_pack"].default is False


def test_nothing_applies_with_the_switch_off_even_when_accepted(tmp_path: Path) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    digest = _pack(ws, {"skills": []})
    packs.accept(home, ws, digest)

    assert packs.applied_pack(_settings(home, on=False), ws) is None
    assert packs.applied_pack(_settings(home), ws) is not None


def test_a_pack_applies_only_to_the_bytes_the_owner_accepted(tmp_path: Path) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    digest = _pack(ws, {"skills": ["a"]})
    settings = _settings(home)

    # Present and readable is not accepted: the file may have come with a clone an hour ago.
    assert packs.applied_pack(settings, ws) is None
    with pytest.raises(packs.PackError, match="changed since it was shown"):
        packs.accept(home, ws, "0" * 64)
    packs.accept(home, ws, digest)
    assert packs.applied_pack(settings, ws) is not None

    # A changed file is a new file: it does not inherit the old acceptance. What applies until the
    # owner looks is the version they accepted — from their record. This asserted `None` before,
    # i.e. that a change lifts the narrowing; the review showed the agent can make that change
    # itself (the file is in its write region), so the premise was the defect, not the code.
    _pack(ws, {"skills": ["a", "b"]})
    held = packs.applied_pack(settings, ws)
    assert held is not None and held.skills == ("a",)

    packs.accept(home, ws, packs.read_pack(ws).digest)
    current = packs.applied_pack(settings, ws)
    assert current is not None and current.skills == ("a", "b")
    assert packs.revoke(home, ws) and packs.applied_pack(settings, ws) is None


def test_keys_a_pack_cannot_set_are_reported_and_not_read(tmp_path: Path) -> None:
    digest = _pack(tmp_path, {
        "skills": ["a"],
        "tools_allow": ["run_shell"],
        "reach": "workspace_shell",
        "verify": "true",
        "mcp_servers": [{"name": "evil", "command": "curl"}],
    })
    found = packs.read_pack(tmp_path)

    assert found.pack is not None and found.digest == digest
    assert found.pack.ignored == ("mcp_servers", "reach", "tools_allow", "verify")
    # The dataclass has no field for any of them: there is nowhere for a widening to land.
    assert set(vars(found.pack)) == {"skills", "mcp", "tools_deny", "ignored", "digest"}


@pytest.mark.parametrize("body", [
    '{"skills": "pdf"}', '["skills"]', "not json", '{"tools_deny": [""]}', '{"mcp": [3]}',
])
def test_a_malformed_pack_is_refused_whole(tmp_path: Path, body: str) -> None:
    _pack(tmp_path, body)
    found = packs.read_pack(tmp_path)
    # Half a pack applied is a file nobody accepted.
    assert found.present and found.pack is None and found.error


# --- skills -------------------------------------------------------------------------------------


def _bundle_block(monkeypatch: pytest.MonkeyPatch, home: Path, ws: Path) -> str:
    """The skills block of an app run in ``ws``: an agent built on the registry the app assembles,
    which is where the pack's skills half is decided (beside its tools half)."""
    from chimera.config import get_settings
    from chimera.core.agent import Agent, AgentConfig

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PROJECT_PACK", "1")
    get_settings.cache_clear()
    ws.mkdir(parents=True, exist_ok=True)
    registry, _ = assemble_registry(CodeSeams(), ws, get_settings(), object(), steps=3)
    agent = Agent(object(), registry, AgentConfig(project_root=ws))  # type: ignore[arg-type]
    return agent._bundle_context()


def test_a_pack_keeps_only_the_switched_on_bundles_it_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    for name in ("pdf-forms", "supabase-admin"):
        _bundle(home, name)
    _bundle(home, "held", status="pending")
    _bundle(home, "parked", status="inactive")
    packs.accept(home, ws, _pack(ws, {"skills": ["pdf-forms", "held", "parked", "never-installed"]}))

    block = _bundle_block(monkeypatch, home, ws)

    assert "pdf-forms" in block
    assert "supabase-admin" not in block, "a switched-on bundle the pack leaves out still reached it"
    # Clamped, not activated: naming a pending bundle in a repository's file is not the owner's
    # switch. Its status on disk is unchanged too.
    assert "held" not in block and "parked" not in block and "never-installed" not in block
    statuses = {b.name: b.status for b in bundles.installed(home)}
    assert statuses["held"] == "pending" and statuses["parked"] == "inactive"


def test_an_empty_skills_list_sends_no_skill_and_an_absent_one_sends_them_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    _bundle(home, "pdf-forms")
    packs.accept(home, ws, _pack(ws, {"skills": []}))
    assert _bundle_block(monkeypatch, home, ws) == ""

    packs.accept(home, ws, _pack(ws, {"tools_deny": ["browser"]}))
    assert "pdf-forms" in _bundle_block(monkeypatch, home, ws)


def test_the_skills_screen_answers_for_the_project_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.api import build_api_app
    from chimera.interface import ChatSession

    ws, home = tmp_path / "ws", tmp_path / "home"
    for name in ("pdf-forms", "supabase-admin"):
        _bundle(home, name)
    packs.accept(home, ws, _pack(ws, {"skills": ["pdf-forms"]}))
    block = _bundle_block(monkeypatch, home, ws)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    client = TestClient(build_api_app(lambda: ChatSession(object()), settings=Settings()))

    shown = client.get("/api/skills/effective", params={"project": str(ws)}).json()

    assert [b["name"] for b in shown["bundles"]] == ["pdf-forms"]
    assert block == "\n\n" + shown["bundle_text"]
    # Without the project, the whole home — what a run outside any folder gets.
    whole = client.get("/api/skills/effective").json()
    assert [b["name"] for b in whole["bundles"]] == ["pdf-forms", "supabase-admin"]


# --- tools and MCP servers ----------------------------------------------------------------------


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "from a connected server"
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **_kw: Any) -> str:
        return "ok"


class _Connector:
    def __init__(self, server: str, *tools: str) -> None:
        self.name = server
        self._tools = [_Tool(f"{server}_{t}") for t in tools]

    def tools(self) -> list[_Tool]:
        return list(self._tools)


class _Pool:
    """`ConnectorRegistry`'s shape: connectors by server name, tools namespaced by server."""

    def __init__(self, *connectors: _Connector) -> None:
        self._by = {c.name: c for c in connectors}

    def names(self) -> list[str]:
        return list(self._by)

    def get(self, name: str) -> _Connector:
        return self._by[name]

    def all_tools(self) -> list[_Tool]:
        return [t for c in self._by.values() for t in c.tools()]

    def into_tool_registry(self, registry: Any) -> int:
        for tool in self.all_tools():
            registry.register(tool)
        return len(self.all_tools())


def _assemble(
    monkeypatch: pytest.MonkeyPatch, ws: Path, settings: Settings, pool: Any
) -> list[str]:
    monkeypatch.setattr(mcp_pool, "connectors", lambda _s: pool)
    registry, _ = assemble_registry(CodeSeams(), ws, settings, object(), steps=3, surface="turn")
    return list(registry.names())


def test_a_pack_hides_the_servers_it_leaves_out_and_cannot_launch_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir()
    pool = _Pool(_Connector("github", "search"), _Connector("supabase", "sql", "tables"))
    packs.accept(home, ws, _pack(ws, {"mcp": ["github", "linear-not-configured"]}))

    names = _assemble(monkeypatch, ws, _settings(home), pool)

    assert "github_search" in names
    assert not [n for n in names if n.startswith("supabase_")]
    assert not [n for n in names if n.startswith("linear")], "a pack conjured a server"


def test_tools_deny_removes_and_never_restores_what_the_owner_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir()
    settings = Settings(  # type: ignore[call-arg]
        CHIMERA_HOME=str(home), CHIMERA_PROJECT_PACK=True, CHIMERA_TOOL_DENYLIST="scrape"
    )
    baseline = _assemble(monkeypatch, ws, settings, None)
    assert "browser" in baseline and "scrape" not in baseline

    # The pack names a tool the owner denied as if granting it, and denies one of its own.
    packs.accept(home, ws, _pack(ws, {
        "tools_deny": ["browser"], "tools_allow": ["scrape"], "skills": ["scrape"],
    }))
    narrowed = _assemble(monkeypatch, ws, settings, None)

    assert "browser" not in narrowed
    assert "scrape" not in narrowed, "a pack undid the owner's denylist"
    assert set(narrowed) <= set(baseline), f"a pack added {set(narrowed) - set(baseline)}"


def test_an_unaccepted_pack_leaves_the_registry_exactly_as_the_settings_make_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir()
    _pack(ws, {"tools_deny": ["browser", "read_file"], "mcp": []})
    pool = _Pool(_Connector("github", "search"))

    names = _assemble(monkeypatch, ws, _settings(home), pool)

    assert "browser" in names and "read_file" in names and "github_search" in names


# --- the routes and the bridge -----------------------------------------------------------------


def _client(monkeypatch: pytest.MonkeyPatch, home: Path, *, on: bool) -> TestClient:
    from chimera.api import build_api_app
    from chimera.config import get_settings
    from chimera.interface import ChatSession

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    monkeypatch.setenv("CHIMERA_PROJECT_PACK", "1" if on else "0")
    get_settings.cache_clear()
    return TestClient(build_api_app(lambda: ChatSession(object()), settings=Settings()))


def test_the_card_lists_what_is_kept_hidden_clamped_and_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    _bundle(home, "pdf-forms")
    _bundle(home, "supabase-admin")
    _bundle(home, "held", status="pending")
    (home / "mcp.json").write_text(
        json.dumps([{"name": "github", "command": "x"}, {"name": "supabase", "command": "y"}]),
        encoding="utf-8",
    )
    digest = _pack(ws, {
        "skills": ["pdf-forms", "held"], "mcp": ["github", "linear"],
        "tools_deny": ["run_shell"], "reach": "workspace_shell",
    })
    client = _client(monkeypatch, home, on=True)

    state = client.get("/api/code/pack", params={"path": str(ws)}).json()
    assert state["present"] and not state["accepted"] and not state["applied"]
    assert state["skills_kept"] == ["pdf-forms"] and state["skills_hidden"] == ["supabase-admin"]
    assert state["skills_not_active"] == ["held"]
    assert state["mcp_kept"] == ["github"] and state["mcp_hidden"] == ["supabase"]
    assert state["mcp_not_configured"] == ["linear"]
    # The critic's point: denying `run_shell` stops the agent running tests itself, so it is on
    # the card, not buried.
    assert state["tools_denied"] == ["run_shell"] and state["ignored"] == ["reach"]

    stale = client.post("/api/code/pack/accept", json={"path": str(ws), "digest": "f" * 64})
    assert stale.status_code == 409
    accepted = client.post("/api/code/pack/accept", json={"path": str(ws), "digest": digest}).json()
    assert accepted["accepted"] and accepted["applied"]

    revoked = client.delete("/api/code/pack", params={"path": str(ws)}).json()
    assert not revoked["accepted"] and not revoked["applied"]


def test_with_the_switch_off_an_accepted_pack_is_shown_as_not_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    packs.accept(home, ws, _pack(ws, {"skills": []}))
    state = _client(monkeypatch, home, on=False).get("/api/code/pack", params={"path": str(ws)}).json()
    assert state["accepted"] and not state["enabled"] and not state["applied"]


def test_the_bridge_cannot_flip_the_switch_nor_accept_a_pack() -> None:
    from chimera.api.bridge_routes import OWNER_ONLY_SETTINGS, ROUTES

    assert "CHIMERA_PROJECT_PACK" in OWNER_ONLY_SETTINGS
    assert not [r for r in ROUTES.values() if r.path.startswith("/api/code/pack")]
