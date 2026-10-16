"""The desktop app's server token stays in the backend: no child process it starts inherits it.

The native shell now starts the backend with ``CHIMERA_SERVER_TOKEN`` set to a per-launch secret, so
that only the app's own window can drive the agent's API. That secret is in ``os.environ`` for the
life of the backend, and anything the backend spawns with a copy of its environment can read it and
call the API past the approval screen. Two paths spawn processes: the sandbox, for the commands the
agent runs, which already filtered secrets; and the pull-request path, which ran gh and git with
``dict(os.environ)`` whole. Nothing failed there, which is how it hid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core import pull_request
from chimera.sandbox.local import _child_env

TOKEN = "per-launch-secret-0123456789abcdef"


@pytest.fixture
def launched_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", TOKEN)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-not-for-children")
    monkeypatch.setenv("CHIMERA_WORKSPACE_MARKER", "kept")


def test_the_sandbox_child_environment_has_no_server_token(launched_with_token: None) -> None:
    env = _child_env()
    assert "CHIMERA_SERVER_TOKEN" not in env
    assert TOKEN not in env.values()
    # The filter removes secrets, not the environment: an ordinary variable still arrives.
    assert env["CHIMERA_WORKSPACE_MARKER"] == "kept"


def test_gh_and_git_run_without_the_server_token_or_provider_keys(
    launched_with_token: None,
) -> None:
    env = pull_request._env()
    assert "CHIMERA_SERVER_TOKEN" not in env
    assert TOKEN not in env.values()
    assert "OPENROUTER_API_KEY" not in env
    assert env["CHIMERA_WORKSPACE_MARKER"] == "kept"
    # The non-interactive overrides are still applied on top.
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GH_PROMPT_DISABLED"] == "1"


@pytest.mark.parametrize(
    "name", ["GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"]
)
def test_gh_keeps_the_credentials_it_is_meant_to_read(
    launched_with_token: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    # Dropping these would turn "open a pull request" into a login prompt that GH_PROMPT_DISABLED
    # refuses, for every user who authenticates gh by environment (CI, a container, a VPS).
    monkeypatch.setenv(name, "ghp_meant_for_gh")
    assert pull_request._env()[name] == "ghp_meant_for_gh"


def test_the_pull_request_process_is_started_with_the_filtered_environment(
    launched_with_token: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        raise OSError("not started in this test")

    monkeypatch.setattr(pull_request.subprocess, "run", fake_run)
    pull_request.run_quiet(["git", "status"], cwd=tmp_path)
    env = seen["env"]
    assert isinstance(env, dict)
    assert "CHIMERA_SERVER_TOKEN" not in env
