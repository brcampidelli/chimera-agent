"""What a shell the agent (or the owner's terminal) starts may inherit from the app's environment.

The old rule was a handful of substrings (`API_KEY`, `TOKEN`, ...). It let through exactly what an
app like this holds most of: `CHIMERA_OPENROUTER_KEYS` (a POOL of keys), `DATABASE_URL` with a
password inside, `AWS_ACCESS_KEY_ID`. Each test names one that got through.
"""

from __future__ import annotations

import pytest

from chimera.api import pty_terminal
from chimera.core.redact import is_secret_env, scrubbed_environ
from chimera.sandbox.local import _child_env

LEAKS = {
    "CHIMERA_OPENROUTER_KEYS": "sk-or-one,sk-or-two",
    "CHIMERA_ANTHROPIC_KEYS": "sk-ant-one",
    "CHIMERA_SERVER_TOKEN": "bearer-token-value",
    "CHIMERA_SCREEN_TOKEN": "screen-token-value",
    "CHIMERA_ENDPOINTS": '[{"name": "x", "api_key": "sk-inside"}]',
    "CHIMERA_SMTP_PASSWORD": "smtp-pw",
    "CHIMERA_GITHUB_WEBHOOK_SECRETS": "whsec",
    "DATABASE_URL": "postgres://app:hunter2@db.internal:5432/prod",
    "REDIS_URL": "redis://:s3cret@cache:6379/0",
    "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
    "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "AWS_SESSION_TOKEN": "FwoGZXIvYXdzEXAMPLE",
    "STRIPE_KEY": "rk_live_xxx",
    "DEPLOY_PASSWORD": "pw",
    "GITHUB_TOKENS": "a,b",
    "SOME_SERVICE_SECRETS": "x",
}
HARMLESS = {
    "PATH": "/usr/bin",
    "HOME": "/home/me",
    "LANG": "en_US.UTF-8",
    "AWS_REGION": "us-east-1",
    "DATABASE_URL_PLAIN": "postgres://db.internal/prod",
    "PUBLIC_URL": "https://example.com/app",
    "CHIMERA_HOME": "/home/me/.chimera",
    # Ordinary app configuration reaches a child shell: the sandbox and job tests hold that contract.
    "CHIMERA_GOVERNANCE": "off",
    "CHIMERA_TEST_HARMLESS": "kept",
    "CHIMERA_WORKSPACE_MARKER": "kept",
}


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {**LEAKS, **HARMLESS}.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("name", sorted(LEAKS))
def test_a_secret_never_reaches_a_child_shell(env: None, name: str) -> None:
    assert is_secret_env(name, LEAKS[name])
    assert name not in scrubbed_environ()
    assert name not in _child_env(), "the agent's shell would read it"
    assert name not in pty_terminal._shell_env(), "the owner's terminal would show it to `env`"


@pytest.mark.parametrize("name", sorted(HARMLESS))
def test_what_a_shell_needs_still_passes(env: None, name: str) -> None:
    assert not is_secret_env(name, HARMLESS[name])
    assert _child_env()[name] == HARMLESS[name]
    assert pty_terminal._shell_env()[name] == HARMLESS[name]


def test_a_password_in_a_url_is_caught_whatever_the_variable_is_called() -> None:
    assert is_secret_env("WHATEVER_THE_NEXT_SERVICE_CALLS_IT", "mysql://root:pw@host/db")
    assert not is_secret_env("WHATEVER", "mysql://host/db")
    assert not is_secret_env("WHATEVER", "https://example.com/a@b")


@pytest.mark.parametrize(
    "name",
    ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"],
)
def test_proxy_variables_pass_even_with_a_password_in_the_url(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """git, pip, npm and gh need them behind an authenticated proxy; the by-value rule must not eat them."""
    value = "http://user:pw@proxy.corp:3128"
    monkeypatch.setenv(name, value)
    assert not is_secret_env(name, value)
    # Windows folds environment names to upper case, so look the name up the way the OS would.
    for env in (scrubbed_environ(), _child_env(), pty_terminal._shell_env()):
        assert {k.upper(): v for k, v in env.items()}[name.upper()] == value
    # ...and the same URL under any other name is still a secret.
    assert is_secret_env("SOME_OTHER_URL", value)


def test_the_agents_shell_keeps_its_non_interactive_overrides_and_the_terminal_does_not(env: None) -> None:
    assert _child_env()["CI"] == "1"
    assert pty_terminal._shell_env().get("CI") != "1"
    assert pty_terminal._shell_env()["CHIMERA_TERMINAL"] == "1"
