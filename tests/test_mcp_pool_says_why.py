"""A server that fails to connect has to say WHY, and the pool is where that sentence was lost.

:mod:`chimera.integrations.mcp_client` was given a message for the one failure a user can actually
act on — *the MCP SDK is not installed — install it with: pip install 'chimera-agent[mcp]'*. The
pool then caught the exception and logged ``ModuleNotFoundError``, which is the half that does not
say what to do. Found by running the real path rather than by reading it: a probe against a venv
without the SDK produced exactly that log line and nothing else.

The bound on the message is deliberate. A connect failure carries the command and its arguments,
and ``mcp.json`` is also where a user's tokens live; ``env`` never appears in an exception message
today, and a log line is the wrong place to bet on that staying true.
"""

from __future__ import annotations

import logging

import pytest

from chimera.config import Settings
from chimera.integrations import mcp_pool


@pytest.fixture(autouse=True)
def _fresh_pool():
    mcp_pool.reset_for_tests()
    yield
    mcp_pool.reset_for_tests()


def _settings(tmp_path, monkeypatch, servers: str) -> Settings:
    """Built through the ENVIRONMENT, because that is the only way these fields are populated.

    Both carry a ``validation_alias``, so ``Settings(mcp_autoload=True)`` is accepted and silently
    ignored — it returns a Settings with autoload OFF. Which produced a first version of this test
    that passed for the wrong reason on the way to failing for another one.
    """
    home = tmp_path / ".chimera"
    home.mkdir(parents=True, exist_ok=True)
    (home / "mcp.json").write_text(servers, encoding="utf-8")
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_MCP_AUTOLOAD", "1")
    settings = Settings()
    assert settings.mcp_autoload and settings.home == home, (
        "the fixture did not produce the settings it claims to — nothing below would be measuring "
        f"anything (home={settings.home}, autoload={settings.mcp_autoload})"
    )
    return settings


def test_a_server_that_cannot_start_says_what_went_wrong(tmp_path, monkeypatch, caplog) -> None:
    """End to end, through the real session — and the reason is ASKED FOR, not assumed.

    A command that does not exist fails for one of two reasons depending on the machine: with the
    SDK installed it is a `FileNotFoundError` naming the command; without it, the import guard fires
    first and it is the sentence about the missing SDK. Both lines are correct.

    The first draft asserted the command's name, which passed on a laptop where the optional
    dependency happened to be installed and went red on all three CI Pythons where it is not — a
    test about a log line that was quietly a test about the environment. So it triggers the failure
    itself, reads whatever message came out, and requires THAT to survive into the log.
    """
    from chimera.integrations import StdioMCPSession

    comando = "comando-que-nao-existe-mesmo"
    try:
        StdioMCPSession(comando, [], None, connect_timeout=5.0).start()
    except Exception as exc:
        motivo = str(exc)
    else:  # pragma: no cover - would mean that name really is an MCP server
        pytest.fail(f"{comando!r} started successfully, so there is no failure to report")

    assert motivo.strip(), "the failure carries no message at all, so nothing could be logged"

    settings = _settings(tmp_path, monkeypatch, f'[{{"name": "quebrado", "command": "{comando}", "args": []}}]')

    with caplog.at_level(logging.WARNING):
        assert mcp_pool.connectors(settings) is None

    linhas = [r.getMessage() for r in caplog.records if "quebrado" in r.getMessage()]
    assert linhas, "a server that failed to connect was skipped silently"
    (linha,) = linhas
    # No disjunction here, deliberately. The first draft read `... in linha or len(linha) > N`,
    # and the fallback clause is true of the OLD line too — sabotaging the fix back to
    # `type(exc).__name__` left this test passing. A guard that survives its own defect is not one.
    assert motivo[:60] in linha, (
        f"the line names the server and not the reason.\n  logged:   {linha!r}\n  reason:   {motivo!r}"
    )


def test_the_sentence_about_the_missing_sdk_survives_the_pool(tmp_path, monkeypatch, caplog) -> None:
    """The failure this whole message exists for, and the one the pool used to swallow.

    `mcp_client.start` raises this exact ModuleNotFoundError so a user without the optional
    dependency is told what to install instead of being handed a timeout that reads as a broken
    config. Through the pool, all that reached the log was the words `ModuleNotFoundError`.
    """
    frase = "the MCP SDK is not installed — install it with: pip install 'chimera-agent[mcp]'"

    class SemSDK:
        def __init__(self, *a, **k) -> None:
            pass

        def start(self):
            raise ModuleNotFoundError(frase)

    monkeypatch.setattr("chimera.integrations.StdioMCPSession", SemSDK)
    settings = _settings(tmp_path, monkeypatch, '[{"name": "srv", "command": "x", "args": []}]')

    with caplog.at_level(logging.WARNING):
        assert mcp_pool.connectors(settings) is None

    (linha,) = [r.getMessage() for r in caplog.records if "srv" in r.getMessage()]
    assert "chimera-agent[mcp]" in linha, (
        f"the one actionable failure still reaches the user as a bare exception class: {linha!r}"
    )


def test_the_reason_is_bounded_so_a_long_failure_cannot_paste_a_config_into_the_log(
    tmp_path, caplog, monkeypatch
) -> None:
    """Guarding the guard: "log the message" must not become "log whatever the message contains"."""
    segredo = "ghp_" + "s" * 4000

    class Explode:
        def __init__(self, *a, **k) -> None:
            pass

        def start(self):
            raise RuntimeError(segredo)

    monkeypatch.setattr("chimera.integrations.StdioMCPSession", Explode)
    settings = _settings(tmp_path, monkeypatch, '[{"name": "longo", "command": "x", "args": []}]')

    with caplog.at_level(logging.WARNING):
        mcp_pool.connectors(settings)

    (linha,) = [r.getMessage() for r in caplog.records if "longo" in r.getMessage()]
    assert len(linha) < 400, f"the reason is unbounded ({len(linha)} chars) — a log is not a dump"
