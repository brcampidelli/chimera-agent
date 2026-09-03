"""Test said "ok, 4 tools" and the next run could not use one of them.

Measured on a live install: a SQLite MCP server was registered, the Test button connected, answered
``ok`` and listed all four tools by name. The run that followed made **twenty-two tool calls over
nineteen minutes and not one of them was from that server** — because ``mcp_autoload`` is off by
default. Nothing on the screen said so.

``ok`` and "the agent can use this" are different facts, and the screen only ever reported the
first. This adds the second, with a reason the app translates rather than an English sentence, and
three states because the remedies differ:

* autoload off — turn it on; the pool is built lazily, so no relaunch is needed;
* autoload on, pool not built — the next run builds it and picks this server up;
* autoload on, pool built without this name — added after the connect, and the pool is never
  rebuilt in a process, so it needs a restart.

The reach read must never BUILD the pool: asking "will the agent see this?" cannot be the thing
that spawns a subprocess per configured server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api import mcp_api
from chimera.integrations import mcp_pool


@pytest.fixture(autouse=True)
def pool_limpo():
    mcp_pool.reset_for_tests()
    yield
    mcp_pool.reset_for_tests()


@pytest.fixture
def casa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with one configured server and a stubbed connect, so nothing spawns."""
    mcp_api.add(tmp_path, "loja", "uvx", ["mcp-alchemy"], {"DB_URL": "sqlite:///x.db"})
    monkeypatch.setattr(
        mcp_api, "_live_test",
        lambda _cfg: [{"name": "execute_query", "description": "run SQL"}],
    )
    return tmp_path


class _Falso:
    """A settings stand-in carrying only what the reach read looks at."""

    def __init__(self, autoload: bool, home: Path) -> None:
        self.mcp_autoload = autoload
        self.home = home


def _com_settings(monkeypatch: pytest.MonkeyPatch, autoload: bool, home: Path) -> None:
    monkeypatch.setattr("chimera.config.get_settings", lambda: _Falso(autoload, home))


# --------------------------------------------------------------------------------------------
# 1. autoload off — it works and no run can use it


def test_a_working_server_reports_that_no_run_can_reach_it(
    casa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _com_settings(monkeypatch, False, casa)

    out = mcp_api.test_server(casa, "loja")

    assert out["ok"] is True, "the server works — that part was never wrong"
    assert out["tools"], "and its tools are listed"
    assert out["reaches_agent"] is False, (
        "a green test beside no reach signal is what let a server sit unused through a "
        "nineteen-minute run"
    )
    assert out["reaches_agent_reason"] == "autoload_off"


def test_the_reason_is_an_enum_and_not_an_english_sentence(
    casa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app ships in ten languages; a server-side sentence is one line in the wrong one."""
    _com_settings(monkeypatch, False, casa)

    razao = mcp_api.test_server(casa, "loja")["reaches_agent_reason"]

    assert razao == "autoload_off"
    assert " " not in razao, "an enum, not prose"


# --------------------------------------------------------------------------------------------
# 2. autoload on — the three states it splits into


def test_autoload_on_with_no_pool_yet_reaches_the_agent(
    casa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not built yet is not the same as empty: the next turn builds it and picks this up."""
    _com_settings(monkeypatch, True, casa)

    out = mcp_api.test_server(casa, "loja")

    assert out["reaches_agent"] is True
    assert out["reaches_agent_reason"] is None


def test_a_server_added_after_the_connect_says_to_restart(
    casa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _com_settings(monkeypatch, True, casa)
    _finge_pool_com(monkeypatch, ("github",))

    out = mcp_api.test_server(casa, "loja")

    assert out["reaches_agent"] is False
    assert out["reaches_agent_reason"] == "added_after_connect", (
        "turning the toggle on cannot fix this one — the pool is never rebuilt in a process"
    )


def test_a_server_already_in_the_pool_reaches_the_agent(
    casa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _com_settings(monkeypatch, True, casa)
    _finge_pool_com(monkeypatch, ("github", "loja"))

    out = mcp_api.test_server(casa, "loja")

    assert out["reaches_agent"] is True
    assert out["reaches_agent_reason"] is None


def _finge_pool_com(monkeypatch: pytest.MonkeyPatch, nomes: tuple[str, ...]) -> None:
    """A pool that is BUILT and holds exactly ``nomes``."""

    class _Pool:
        def names(self) -> tuple[str, ...]:
            return nomes

    monkeypatch.setattr(mcp_pool, "_pool", _Pool())
    monkeypatch.setattr(mcp_pool, "_tried", True)


def test_a_pool_that_was_built_and_came_back_empty_is_not_reported_as_pending(
    casa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_tried` latches, so "not built yet" would promise a pickup that is never coming."""
    _com_settings(monkeypatch, True, casa)
    monkeypatch.setattr(mcp_pool, "_pool", None)
    monkeypatch.setattr(mcp_pool, "_tried", True)

    out = mcp_api.test_server(casa, "loja")

    assert out["reaches_agent"] is False
    assert out["reaches_agent_reason"] == "added_after_connect"


# --------------------------------------------------------------------------------------------
# 3. Asking must not be the thing that answers


def test_reading_the_reach_never_builds_the_pool(
    casa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _com_settings(monkeypatch, True, casa)
    construiu = []
    monkeypatch.setattr(mcp_pool, "_build", lambda _s: construiu.append(1))

    mcp_api.test_server(casa, "loja")

    assert construiu == [], (
        "a screen asking whether the agent can see a server must not spawn a subprocess per "
        "configured server to find out"
    )
    assert mcp_pool._tried is False


def test_a_failed_connect_still_reports_the_reach(
    casa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken server and an unreachable one are different problems; report both."""
    _com_settings(monkeypatch, False, casa)
    monkeypatch.setattr(mcp_api, "_live_test", lambda _c: (_ for _ in ()).throw(TimeoutError("slow")))

    out = mcp_api.test_server(casa, "loja")

    assert out["ok"] is False
    assert out["error"]
    assert out["reaches_agent"] is False


def test_an_unknown_server_still_reports_the_reach(
    casa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _com_settings(monkeypatch, False, casa)

    out = mcp_api.test_server(casa, "nao-existe")

    assert out["ok"] is False
    assert out["error"] == "no such server"
    assert "reaches_agent" in out


def test_the_reach_read_never_raises(casa: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown is a real third state — a status read must not turn a working test into a 500."""
    monkeypatch.setattr(
        "chimera.config.get_settings", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    out = mcp_api.test_server(casa, "loja")

    assert out["ok"] is True
    assert out["reaches_agent"] is None
    assert out["reaches_agent_reason"] is None


# --------------------------------------------------------------------------------------------
# 4. The state read itself


def test_pool_state_distinguishes_not_built_from_empty() -> None:
    from chimera.integrations.mcp_pool import PoolState, pool_state

    e = pool_state(_Falso(True, Path(".")))
    assert isinstance(e, PoolState)
    assert e.connected is None, "nothing has been built, which is not the same as nothing exists"
    assert e.built is False
    assert e.autoload is True
