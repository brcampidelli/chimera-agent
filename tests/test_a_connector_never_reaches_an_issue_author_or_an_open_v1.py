"""The owner's OpenAPI connectors stay off the two other doors a stranger can knock on.

#938 closed the bots: a GET runs with the owner's key, so on a bot that answers anyone the owner's
private API answered anyone too. The same investigation found two more surfaces where the person
driving the turn need not be the owner, and the owner decided (2026-10-16) to close both with the
same rule — no connector where someone other than the owner can start the run, unless an owner's
allowlist guarantees it is the owner:

* ``github-issue`` — the task IS the issue: its title and body, and a ``/chimera`` comment, written by
  whoever can open an issue or comment on one. The only allowlist is of REPOSITORIES
  (``CHIMERA_GITHUB_ISSUE_REPOSITORIES``); nothing checks the author, so no allowlist makes it the
  owner's and it never loads one.
* ``/v1/chat/completions`` — mounted every switched-on connector through a path that never asked
  ``unattended_surface_may_load``, behind a guard that is a no-op until ``CHIMERA_SERVER_TOKEN`` is
  set. Without the token, whoever reaches the port is the caller; with it, only the owner is, and
  loading stays as it was.

The attended surfaces (the Code screen's turn, the app's chat, the terminal ones) and the cron do not
change, and are asserted here too — a fix that refused everywhere would pass the first half.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.integrations import openapi_store as store

SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Pets", "version": "1"},
    "servers": [{"url": "https://api.pets.example"}],
    "paths": {"/pets": {"get": {"operationId": "listPets", "summary": "List pets"}}},
}
TOOL = "api_pets_listPets"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.scrape.ssrf as ssrf

    monkeypatch.setattr(ssrf, "_resolve_ips", lambda host: ["93.184.216.34"])
    monkeypatch.delenv("CHIMERA_SERVER_TOKEN", raising=False)


def _home(tmp_path: Path, **changes: Any) -> Path:
    home = tmp_path / "home"
    spec = tmp_path / "pets.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    store.add(home, "pets", str(spec))
    store.update(home, "pets", {"enabled": True, **changes})
    return home


def _settings(tmp_path: Path, **update: Any) -> Any:
    """No bot configured — so nothing #938 refuses — whatever the developer's environment holds."""
    from chimera.config import Settings
    from chimera.server.allowlist import CONNECTION_FIELDS

    base = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    cleared: dict[str, Any] = {f: None for fs in CONNECTION_FIELDS.values() for f in fs}
    return base.model_copy(update={**cleared, "server_token": None, **update})


def _governed_names(tmp_path: Path, settings: Any, surface: str) -> set[str]:
    from chimera.governance.profile import governed_profile
    from chimera.tools import default_registry

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    registry, _ = governed_profile(
        default_registry(ws), settings=settings, home=settings.home, surface=surface
    )
    return set(registry.names())


# ------------------------------------------------------------------------------- github-issue


def test_an_issue_job_loads_no_connector_even_one_the_owner_sent_to_unattended_surfaces(
    tmp_path: Path,
) -> None:
    """Before: every connector marked ``unattended`` mounted here, because the refusal only looked at
    the bots in ``ALLOWLIST_FIELDS``. A stranger writing "call api_pets_listPets and paste the result
    into the PR" in an issue on an enabled repository had the owner's key run for them."""
    _home(tmp_path, unattended=True)
    settings = _settings(tmp_path, github_issue_repositories=["owner/repo"])

    assert store.unattended_surface_may_load(settings, "github-issue")
    assert TOOL not in _governed_names(tmp_path, settings, "github-issue"), "an issue author reached it"
    # The owner's token on the HTTP gateway says nothing about who wrote the issue.
    with_token = settings.model_copy(update={"server_token": "owner-secret"})
    assert TOOL not in _governed_names(tmp_path, with_token, "github-issue")


def test_the_issue_surface_is_the_one_the_webhook_job_runs_under() -> None:
    """The refusal is keyed on the family; if the job were renamed it would quietly stop applying."""
    import inspect

    import chimera.cli.commands.serve as serve
    from chimera.governance.surfaces import is_issue_surface

    source = inspect.getsource(serve.serve)
    assert 'surface="github-issue"' in source
    assert is_issue_surface("github-issue") and is_issue_surface("github-issue:owner/repo")
    assert not is_issue_surface("github") and not is_issue_surface("cron:github-issue")


def test_the_cron_and_the_owner_only_surfaces_still_load_what_the_owner_sent_there(
    tmp_path: Path,
) -> None:
    _home(tmp_path, unattended=True)
    settings = _settings(tmp_path)
    for surface in ("cron:task", "cron:daily-report", "kanban-solve", "mcp", "a2a"):
        assert store.unattended_surface_may_load(settings, surface) == "", surface
        assert TOOL in _governed_names(tmp_path, settings, surface), surface
    for surface in ("api:turn", "chat", "tui", "assist", "serve-v1-guarded"):
        assert store.unattended_surface_may_load(settings, surface) == "", surface


# ---------------------------------------------------------------------- /v1/chat/completions


def _app_factories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> dict[str, Any]:
    """The two factories ``chimera app`` hands ``build_api_app``, taken from that call itself."""
    from typer.testing import CliRunner

    import chimera.api as api_pkg
    import chimera.cli.main as cli
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "0")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    captured: dict[str, Any] = {}

    def fake_build_api_app(factory: Any, **kwargs: Any) -> Any:
        captured["factory"] = factory
        captured["openai_factory"] = kwargs.get("openai_factory")
        raise SystemExit(0)

    monkeypatch.setattr(api_pkg, "build_api_app", fake_build_api_app)
    CliRunner().invoke(cli.app, ["app", "--workspace", str(tmp_path / "ws"), "--no-open", "--no-cron"])
    assert "factory" in captured, "the command never reached build_api_app"
    return captured


def _session_names(session: Any) -> set[str]:
    return set(session.agent.tools.names())


def test_v1_without_the_owners_token_loads_no_connector_and_the_apps_chat_still_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before: the unguarded session mounted every switched-on connector — not even only the ones
    sent to unattended surfaces — and its guard (``_require_token``) passes everyone while
    ``CHIMERA_SERVER_TOKEN`` is unset, the shipped default. So whoever could reach the port could
    POST "call api_pets_listPets" and read the owner's API through the owner's key."""
    _home(tmp_path)  # switched on for the app; NOT sent to the unattended surfaces
    (tmp_path / "ws").mkdir()
    factories = _app_factories(tmp_path, monkeypatch)
    from chimera.config import get_settings

    try:
        assert TOOL not in _session_names(factories["openai_factory"]()), "/v1 reached a stranger"
        assert TOOL in _session_names(factories["factory"]()), "the app's own chat must not change"
    finally:
        get_settings.cache_clear()


def test_v1_with_the_owners_token_loads_as_it_always_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the token set, every ``/v1`` request has proven it is the owner's — the allowlist case."""
    _home(tmp_path)
    (tmp_path / "ws").mkdir()
    factories = _app_factories(tmp_path, monkeypatch, CHIMERA_SERVER_TOKEN="owner-secret")
    from chimera.config import get_settings

    try:
        assert TOOL in _session_names(factories["openai_factory"]())
        assert TOOL in _session_names(factories["factory"]())
    finally:
        get_settings.cache_clear()


def test_the_v1_rule_reads_the_token_and_names_the_family(tmp_path: Path) -> None:
    from chimera.governance.surfaces import is_owner_token_surface

    open_v1 = _settings(tmp_path)
    assert "CHIMERA_SERVER_TOKEN" in store.unattended_surface_may_load(open_v1, "serve-v1")
    closed = open_v1.model_copy(update={"server_token": "owner-secret"})
    assert store.unattended_surface_may_load(closed, "serve-v1") == ""
    assert is_owner_token_surface("serve-v1") and not is_owner_token_surface("serve-v1-guarded")
