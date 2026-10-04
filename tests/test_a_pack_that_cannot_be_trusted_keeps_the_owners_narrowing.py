"""A project's pack keeps the narrowing the owner accepted, decides both halves in one place, and
cannot be used to load, lift or lose anything by a file the agent can write.

Study 29, P7.6, after the adversarial review. Each test is one finding that held:

* **Tamper.** The agent runs with write tools in the folder that holds `.chimera/pack.json`. Before,
  any change to that file — one appended space — changed its digest, the acceptance stopped
  matching, and the next turn got back `run_shell` and every MCP server the owner had taken away.
  Now the version the owner accepted keeps applying, from their record, until they accept the new
  file or revoke. Editing, breaking and deleting the file are all the same case.
* **One decision, two halves.** The assembly that narrows a run's tools stamps the skills half on
  the registry it returns, and the agent reads it there. Read from `project_root` instead, skills
  were narrowed where tools were not (scheduled jobs, terminal, bots) and not narrowed where tools
  were (a crew worker's temporary worktree; a hierarchy worker with no root at all).
* **Hidden servers are never asked.** Denying a hidden server's tools needed a second listing per
  turn, and a transient failure of that one left them registered. Now the pool is narrowed before
  anything is listed.
* **Bounded read**, **a named folder** for accept/revoke, and **no lost acceptance** when two
  windows accept at once.
"""

from __future__ import annotations

import json
import threading
import time
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


def _bundle(home: Path, name: str) -> None:
    root = bundles.bundles_root(home) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    (root / "bundle.json").write_text(
        # Switched on the way `set_status` records it (with the time), as an owner's switch is.
        json.dumps(
            {"name": name, "description": f"The {name} skill.", "status": "active", "switched_on_at": "2026-10-03T00:00:00+00:00"}
        ),
        encoding="utf-8",
    )


def _pack(folder: Path, body: Any) -> str:
    path = folder / packs.PACK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((body if isinstance(body, str) else json.dumps(body)).encode("utf-8"))
    return packs.read_pack(folder).digest


def _settings(home: Path, **extra: Any) -> Settings:
    return Settings(CHIMERA_HOME=str(home), CHIMERA_PROJECT_PACK=True, **extra)  # type: ignore[call-arg]


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "from a connected server"
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **_kw: Any) -> str:
        return "ok"


class _Connector:
    """A server that counts how often it is asked for its tools, and can refuse to answer."""

    def __init__(self, server: str, *tools: str, fails: bool = False) -> None:
        self.name = server
        self._tools = [_Tool(f"{server}_{t}") for t in tools]
        self.fails = fails
        self.listed = 0

    def tools(self) -> list[_Tool]:
        self.listed += 1
        if self.fails:
            raise ConnectionError("transient")
        return list(self._tools)


def _pool(*connectors: _Connector) -> Any:
    from chimera.integrations.connectors import ConnectorRegistry

    pool = ConnectorRegistry()
    for c in connectors:
        pool.register(c)  # type: ignore[arg-type]
    return pool


def _names(monkeypatch: pytest.MonkeyPatch, ws: Path, settings: Settings, pool: Any) -> list[str]:
    monkeypatch.setattr(mcp_pool, "connectors", lambda _s: pool)
    registry, _ = assemble_registry(CodeSeams(), ws, settings, object(), steps=3, surface="turn")
    return list(registry.names())


# --- tamper ---------------------------------------------------------------------------------------


def _tamper_append_space(ws: Path) -> None:
    path = ws / packs.PACK_PATH
    path.write_bytes(path.read_bytes() + b" ")


def _tamper_widen(ws: Path) -> None:
    _pack(ws, {"tools_deny": [], "mcp": ["github", "supabase"]})


def _tamper_break(ws: Path) -> None:
    (ws / packs.PACK_PATH).write_text("{not json", encoding="utf-8")


def _tamper_delete(ws: Path) -> None:
    (ws / packs.PACK_PATH).unlink()


@pytest.mark.parametrize(
    "tamper", [_tamper_append_space, _tamper_widen, _tamper_break, _tamper_delete]
)
def test_changing_the_file_does_not_hand_back_what_the_owner_took_away(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tamper: Any
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir()
    settings = _settings(home)
    pool = _pool(_Connector("github", "search"), _Connector("supabase", "sql"))
    packs.accept(home, ws, _pack(ws, {"tools_deny": ["run_shell"], "mcp": ["github"]}))
    before = _names(monkeypatch, ws, settings, pool)
    assert "run_shell" not in before and "supabase_sql" not in before

    tamper(ws)
    after = _names(monkeypatch, ws, settings, pool)

    assert "run_shell" not in after, "a change to the file lifted the owner's denial"
    assert "supabase_sql" not in after, "a change to the file brought a hidden server back"
    assert "github_search" in after


def test_the_card_says_the_accepted_version_still_applies_and_revoke_lifts_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    packs.accept(home, ws, _pack(ws, {"tools_deny": ["run_shell"]}))
    client = _client(monkeypatch, home)

    _tamper_append_space(ws)
    changed = client.get("/api/code/pack", params={"path": str(ws)}).json()
    assert changed["changed"] and changed["held"] and changed["applied"]
    assert not changed["accepted"]

    # Even with the file gone, the card has something to show — and a revoke to offer.
    _tamper_delete(ws)
    gone = client.get("/api/code/pack", params={"path": str(ws)}).json()
    assert not gone["present"] and gone["held"] and gone["applied"]

    lifted = client.delete("/api/code/pack", params={"path": str(ws)}).json()
    assert not lifted["held"] and not lifted["applied"]
    assert packs.applied_pack(_settings(home), ws) is None


# --- one decision, both halves ---------------------------------------------------------------------


def _prompt_skills(registry: Any, monkeypatch: pytest.MonkeyPatch, home: Path, root: Any) -> str:
    from chimera.config import get_settings
    from chimera.core.agent import Agent, AgentConfig

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PROJECT_PACK", "1")
    get_settings.cache_clear()
    agent = Agent(object(), registry, AgentConfig(project_root=root))  # type: ignore[arg-type]
    return agent._bundle_context()


def test_a_crew_worker_in_a_worktree_gets_the_projects_skills_as_well_as_its_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project, worktree, home = tmp_path / "project", tmp_path / "wt-1", tmp_path / "home"
    worktree.mkdir()
    for name in ("pdf-forms", "supabase-admin"):
        _bundle(home, name)
    packs.accept(home, project, _pack(project, {"skills": ["pdf-forms"], "tools_deny": ["browser"]}))
    monkeypatch.setattr(mcp_pool, "connectors", lambda _s: None)

    # What `POST /api/orchestration/crew` builds: the worker's copy, the project's grant.
    registry, _ = assemble_registry(
        CodeSeams(), worktree, _settings(home), object(), steps=3, grant_root=project
    )
    block = _prompt_skills(registry, monkeypatch, home, worktree)

    assert "browser" not in registry.names()
    assert "pdf-forms" in block and "supabase-admin" not in block, (
        "the worker's tools were narrowed by the project's pack and its skills were not"
    )


def test_a_role_with_its_own_tool_list_keeps_the_packs_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.orchestration.roles import _restrict_tools

    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir()
    for name in ("pdf-forms", "supabase-admin"):
        _bundle(home, name)
    packs.accept(home, ws, _pack(ws, {"skills": ["pdf-forms"]}))
    monkeypatch.setattr(mcp_pool, "connectors", lambda _s: None)
    registry, _ = assemble_registry(CodeSeams(), ws, _settings(home), object(), steps=3)

    narrowed = _restrict_tools(registry, ["read_file"])

    assert narrowed.bundle_only == frozenset({"pdf-forms"})


def test_every_registry_derived_from_a_narrowed_run_keeps_its_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A subagent, a governed or ledgered wrapping, an allowlist, the skill aliases and the research
    subset each build a NEW registry from the run's. One that dropped the stamp handed its run
    every bundle the pack kept out of the parent's prompt, with the parent's tools still narrowed."""
    from chimera.core.research import web_research_registry
    from chimera.core.subagent import SubAgentTool
    from chimera.ecosystem.meta_agent import AgentBlueprint, MetaAgent
    from chimera.governance.allowlist import restrict_registry
    from chimera.governance.governed_tool import govern_registry
    from chimera.governance.kernel import TrustKernel
    from chimera.governance.ledger import TaintLedger
    from chimera.governance.ledger_tool import ledger_registry
    from chimera.skills.aliases import adapt_registry

    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir()
    packs.accept(home, ws, _pack(ws, {"skills": ["pdf-forms"]}))
    monkeypatch.setattr(mcp_pool, "connectors", lambda _s: None)
    registry, _ = assemble_registry(CodeSeams(), ws, _settings(home), object(), steps=3)
    keep = frozenset({"pdf-forms"})
    assert registry.bundle_only == keep

    derived = {
        "allowlist": restrict_registry(registry, allow=["read_file"]),
        "governed": govern_registry(registry, TrustKernel()),
        "ledgered": ledger_registry(registry, TaintLedger()),
        "aliases": adapt_registry(registry),
        "research": web_research_registry(registry),
        "subagent": SubAgentTool(object(), registry)._build_registry(["read_file"]),  # type: ignore[arg-type]
    }
    blueprint = AgentBlueprint(name="w", role_prompt="p", tools=["read_file"])
    meta = MetaAgent(object(), allowed_tools=["read_file"])  # type: ignore[arg-type]
    derived["blueprint"] = meta.build(blueprint, tools=registry).tools  # type: ignore[assignment]

    lost = sorted(name for name, reg in derived.items() if getattr(reg, "bundle_only", None) != keep)
    assert not lost, f"these derivations dropped the pack's skills half: {lost}"
    # And the home the run was assembled in (see
    # `test_the_prompt_reads_the_skills_of_the_settings_the_run_was_built_with.py`).
    assert registry.bundle_home is not None
    elsewhere = sorted(
        name for name, reg in derived.items() if getattr(reg, "bundle_home", None) != registry.bundle_home
    )
    assert not elsewhere, f"these derivations dropped the run's skills home: {elsewhere}"


def test_a_surface_that_does_not_read_packs_narrows_neither_half(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scheduled job, the terminal and the bots build their own registry and never apply a
    pack's tools; their skills are not narrowed either, which is what the card now says."""
    from chimera.tools import default_registry

    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir()
    for name in ("pdf-forms", "supabase-admin"):
        _bundle(home, name)
    packs.accept(home, ws, _pack(ws, {"skills": ["pdf-forms"], "tools_deny": ["browser"]}))

    registry = default_registry(ws)
    block = _prompt_skills(registry, monkeypatch, home, ws)

    assert "browser" in registry.names()
    assert "pdf-forms" in block and "supabase-admin" in block


# --- hidden servers are never listed ---------------------------------------------------------------


@pytest.mark.parametrize("defer", [False, True])
def test_a_hidden_server_is_never_asked_for_its_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, defer: bool
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir()
    hidden = _Connector("supabase", "sql", fails=False)
    kept = _Connector("github", "search")
    packs.accept(home, ws, _pack(ws, {"mcp": ["github"]}))

    names = _names(monkeypatch, ws, _settings(home, CHIMERA_MCP_DEFER=defer), _pool(kept, hidden))

    assert hidden.listed == 0, "a server the pack hides was still listed"
    assert kept.listed >= 1
    assert "supabase_sql" not in names


def test_a_hidden_server_whose_listing_fails_is_still_hidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fail-open the review simulated: the first listing worked and registered the tools, the
    second (the one that computed the denials) failed. With one listing of the kept servers only,
    there is no second listing to fail."""
    ws, home = tmp_path / "ws", tmp_path / "home"
    ws.mkdir()

    class _Flaky(_Connector):
        def tools(self) -> list[_Tool]:
            self.listed += 1
            if self.listed > 1:
                raise ConnectionError("transient")
            return list(self._tools)

    hidden = _Flaky("supabase", "sql")
    packs.accept(home, ws, _pack(ws, {"mcp": ["github"]}))

    names = _names(monkeypatch, ws, _settings(home), _pool(_Connector("github", "search"), hidden))

    assert "supabase_sql" not in names


# --- bounded read, a named folder, no lost acceptance -----------------------------------------------


def test_an_oversized_pack_is_refused_without_being_loaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / packs.PACK_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(b" " * (packs.MAX_PACK_BYTES + 1))

    def whole_file(self: Path) -> bytes:
        raise AssertionError(f"{self} was read whole")

    monkeypatch.setattr(Path, "read_bytes", whole_file)
    found = packs.read_pack(tmp_path)

    assert found.present and found.pack is None and "larger than" in found.error


def _client(monkeypatch: pytest.MonkeyPatch, home: Path) -> TestClient:
    from chimera.api import build_api_app
    from chimera.config import get_settings
    from chimera.interface import ChatSession

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    monkeypatch.setenv("CHIMERA_PROJECT_PACK", "1")
    get_settings.cache_clear()
    return TestClient(build_api_app(lambda: ChatSession(object()), settings=Settings()))


@pytest.mark.parametrize("blank", ["", "   ", "relative/folder"])
def test_a_blank_or_relative_folder_changes_no_acceptance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, blank: str
) -> None:
    ws, home = tmp_path / "ws", tmp_path / "home"
    digest = _pack(ws, {"tools_deny": ["run_shell"]})
    packs.accept(home, ws, digest)
    client = _client(monkeypatch, home)
    # The sidecar's own working directory is the accepted folder: a blank path resolved here.
    monkeypatch.chdir(ws)

    revoked = client.delete("/api/code/pack", params={"path": blank})
    accepted = client.post("/api/code/pack/accept", json={"path": blank, "digest": digest})

    assert revoked.status_code == 400 and accepted.status_code == 400
    assert packs.accepted_digest(home, ws) == digest


def test_two_acceptances_at_once_are_both_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    folders = [tmp_path / f"p{i}" for i in range(2)]
    digests = [_pack(f, {"skills": [f.name]}) for f in folders]
    real_read = packs._read_consents

    def slow_read(where: Path) -> dict[str, dict[str, Any]]:
        # Widens the window between reading the record and writing it back, so two unserialised
        # writers both read before either writes — deterministically, not by luck.
        out = real_read(where)
        time.sleep(0.2)
        return out

    monkeypatch.setattr(packs, "_read_consents", slow_read)
    threads = [
        threading.Thread(target=packs.accept, args=(home, f, d))
        for f, d in zip(folders, digests, strict=True)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert [packs.accepted_digest(home, f) for f in folders] == digests
    assert not list(home.glob("*.tmp")), "a temporary file was left behind"
