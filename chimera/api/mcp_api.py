"""Read/write + live-test the configured MCP servers for the desktop app's MCP screen.

Honesty is the whole point of this module:

- **Config reads/writes are cheap file I/O — they NEVER connect.** ``list_servers``/``add``/``remove``
  only touch ``.chimera/mcp.json`` (via :mod:`chimera.integrations.mcp_config`). A server appearing in
  the list means "configured", never "connected".
- **``env`` VALUES are never returned.** ``list_servers`` reports only the env KEY names (``env_keys``);
  the secret values stay in the local store, never crossing the API.
- **``test`` is the ONLY connecting call**, and it is the ONLY thing that can prove a server is live: a
  real stdio connect + tool enumeration. Every failure is caught and flattened to a short, secret-free
  ``{ok:false, tools:[], error}`` — never a stack trace, never an env value, never a 500.
- **The last test is remembered, and remembered as history.** ``mcp_tests.json`` keeps, per server,
  whether the last test passed, how many tools it listed and when — so the screen can say "tested
  at 14:02, 4 tools" after a relaunch instead of nothing. It is NEVER the "connected" signal: a test
  from yesterday says nothing about whether the server starts today, and the screen that showed it
  as "connected" would be making the claim this module exists to refuse. It keeps no tool names, no
  descriptions (third-party text) and no env value.
- **A held server is shown with its diff.** When a server's tools changed since the owner approved them,
  the mount is held (:mod:`chimera.integrations.mcp_pins`), and ``list_servers`` carries the change as
  ``manifest_held`` — read from the pin file, no connect — so the screen can show the owner what they
  are being asked to approve. ``approve_manifest`` is the owner's answer.
- **Test annotates pushy descriptions.** Each listed tool carries ``cues``: codes for phrases that
  try to steer which tool the model picks (:mod:`chimera.integrations.mcp_cues`). An annotation; it
  refuses nothing.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

from chimera.integrations.mcp_config import (
    TESTS_LOCK,
    McpServerConfig,
    add_server,
    load_servers,
    load_test_records,
    probe_tools,
    remove_server,
    save_test_records,
    tests_path_for,
)
from chimera.integrations.mcp_cues import selection_cues
from chimera.integrations.mcp_pins import approve_change, held_change
from chimera.telemetry import get_logger

_log = get_logger("api.mcp")

# A test connect is bounded so a misbehaving server can't hang the request thread.
_TEST_CONNECT_TIMEOUT = 12.0
# ...except that some servers cannot answer until a PERSON has signed in through the browser, and a
# person is not a server. Twelve seconds was set for a process that answers or does not; it was
# survivable for a sign-in only because a timed-out probe used to LEAK its process, which went on
# waiting and finished the login in the background. Now that a timed-out probe is torn down (see
# :func:`chimera.integrations.mcp_config.probe_tools`), the sign-in has to fit inside the test.
# mcp-remote alone waits 30 s for the browser's callback, after npx may have spent a while
# downloading it; two minutes covers both with room, and it is still a bound.
_TEST_SIGNIN_TIMEOUT = 120.0

# Every read-modify-write of mcp_tests.json goes through this lock, and it is the STORE's lock: the
# store forgets a record whenever a server is added or removed (by the app or the CLI), and a Test
# remembering one at the same moment has to wait for it, not interleave with it.
_TESTS_LOCK = TESTS_LOCK


def _mcp_path(home: Path) -> Path:
    return Path(home) / "mcp.json"


def _tests_path(home: Path) -> Path:
    return tests_path_for(_mcp_path(home))


def _fingerprint(cfg: McpServerConfig) -> str:
    """What the remembered test was ABOUT: the command, its arguments and the env key NAMES.

    A result recorded against ``npx foo`` and shown beside a server that is now ``docker bar`` is a
    true sentence about a different server. Every edit through the store - the app's Add and Remove,
    and ``chimera mcp add/remove`` - drops the record outright (see
    :func:`chimera.integrations.mcp_config.add_server`); this catches a hand edit of ``mcp.json``.
    Env VALUES are left out on purpose - a hash of a short token is a token with extra steps - so
    only a hand edit that changes nothing but a token's value keeps the record.
    """
    payload = json.dumps(
        {"command": cfg.command, "args": list(cfg.args), "env_keys": sorted(cfg.env)},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _load_tests(home: Path) -> dict[str, dict[str, Any]]:
    # Missing or unreadable is the same answer: no test is remembered.
    return load_test_records(_tests_path(home))


def _save_tests(home: Path, tests: dict[str, dict[str, Any]]) -> None:
    save_test_records(_tests_path(home), tests)


def _remember(home: Path, cfg: McpServerConfig, *, ok: bool, tool_count: int) -> None:
    try:
        with _TESTS_LOCK:
            # The server may have been replaced while it was being tested: Add with a new token
            # under the same key names, which the fingerprint cannot tell apart because it leaves
            # env VALUES out. Add has already forgotten the old record, and writing this one now
            # would put the old token's "ok" back beside the new token. So the result is kept only
            # if the configuration that was tested, values included, is still the one on disk.
            current = next((s for s in load_servers(_mcp_path(home)) if s.name == cfg.name), None)
            if current != cfg:
                return
            tests = _load_tests(home)
            tests[cfg.name] = {
                "ok": ok,
                "tool_count": tool_count,
                "tested_at": time.time(),
                "fingerprint": _fingerprint(cfg),
            }
            _save_tests(home, tests)
    except OSError as exc:  # a full disk must not turn a passing test into a failing request
        _log.warning("could not remember the MCP test for %r: %s", cfg.name, type(exc).__name__)


def _last_test(tests: dict[str, dict[str, Any]], cfg: McpServerConfig) -> dict[str, Any] | None:
    """The remembered test for ``cfg``, or None when there is none or it was about another config."""
    rec = tests.get(cfg.name)
    if not isinstance(rec, dict) or rec.get("fingerprint") != _fingerprint(cfg):
        return None
    # The file is ours, but it is also a file: edited by hand, written by another tool, half-copied.
    # A JSON number can be read back as an infinity or NaN, and each of those used to break the
    # list rather than this record - int(inf) raises OverflowError, which was not caught, and a NaN
    # or infinite `tested_at` passed float() and then failed the response's JSON encoding, a 500
    # on GET /api/mcp. A record that cannot be a real test is no record.
    try:
        tool_count = int(rec["tool_count"])
        tested_at = float(rec["tested_at"])
        ok = bool(rec["ok"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if tool_count < 0 or not math.isfinite(tested_at):
        return None
    return {"ok": ok, "tool_count": tool_count, "tested_at": tested_at}


def list_servers(home: Path) -> dict[str, Any]:
    """The configured servers as ``{servers:[{name, command, args, env_keys, last_test}], count}``.

    No connect. ``env_keys`` is the SORTED list of env variable NAMES only — the values are never
    returned. ``last_test`` is the remembered result of the last Test (``{ok, tool_count,
    tested_at}``) or null; it is history, never a claim that the server is connected now.
    """
    servers = load_servers(_mcp_path(home))
    with _TESTS_LOCK:
        tests = _load_tests(home)
    out = [
        {
            "name": s.name,
            "command": s.command,
            "args": list(s.args),
            "env_keys": sorted(s.env),
            "last_test": _last_test(tests, s),
            "manifest_held": _held(home, s.name),
        }
        for s in servers
    ]
    return {"servers": out, "count": len(out)}


def _held(home: Path, name: str) -> dict[str, Any] | None:
    """The change waiting for the owner, each changed tool annotated with the cues of its NEW text.

    The cues are computed on the new description because that is the text the owner is being asked
    to let through; the old one was already approved.
    """
    held = held_change(_mcp_path(home), name)
    if held is None:
        return None
    for change in held["changes"]:
        change["cues"] = selection_cues(change["new_description"])
    return held


def approve_manifest(home: Path, name: str) -> bool:
    """Accept the held tool manifest of ``name``. False when nothing was held for it.

    Takes effect on the next connect: the servers are connected once per process
    (:mod:`chimera.integrations.mcp_pool`), and the held one was not.
    """
    return approve_change(_mcp_path(home), name)


def add(home: Path, name: str, command: str, args: list[str], env: dict[str, str]) -> dict[str, Any]:
    """Add (or replace-by-name) a server, then return the refreshed list (env values still masked)."""
    cfg = McpServerConfig(name=name, command=command, args=list(args), env=dict(env))
    # Replacing a server by name is a new server as far as a test is concerned - possibly a new
    # token under the same key names, which the fingerprint cannot see. The store forgets the old
    # record itself, so the CLI's add, which never comes through here, forgets it too.
    add_server(_mcp_path(home), cfg)
    return list_servers(home)


def remove(home: Path, name: str) -> bool:
    """Remove a server by name. Returns True if one was removed."""
    # The store forgets the remembered test as well (see add).
    return remove_server(_mcp_path(home), name)


def _signs_in_through_the_browser(cfg: McpServerConfig) -> bool:
    """Whether connecting ``cfg`` may wait on a person signing in through the browser.

    Two ways to know, both declarations rather than guesses about a stranger's package: the
    ``mcp-remote`` bridge, whose OAuth flow opens the browser whenever the remote server answers
    401 (Notion and Supabase on purpose, Stripe when its key is refused); and a catalogue entry that
    declares ``auth="oauth"`` and is still configured exactly as the catalogue wrote it.
    """
    from chimera.integrations.mcp_catalog import CATALOG

    if any("mcp-remote" in arg for arg in cfg.args):
        return True
    return any(
        e.auth == "oauth" and e.command == cfg.command and list(e.args) == list(cfg.args)
        for e in CATALOG
    )


def _test_timeout(cfg: McpServerConfig) -> float:
    return _TEST_SIGNIN_TIMEOUT if _signs_in_through_the_browser(cfg) else _TEST_CONNECT_TIMEOUT


def _live_test(cfg: McpServerConfig) -> list[dict[str, str]]:
    """Connect ``cfg`` and return its tools as ``[{name, description}]``. Isolated so tests can
    monkeypatch it (``chimera.api.mcp_api._live_test``) without spawning a real subprocess."""
    return probe_tools(cfg, connect_timeout=_test_timeout(cfg))


def test_server(home: Path, name: str) -> dict[str, Any]:
    """Live-connect the named server and report its tools, or a short secret-free error. Never raises.

    ``{ok:true, tools:[{name, description}], error:null}`` on a real connect; ``{ok:false, tools:[],
    error}`` on ANY failure (unknown server, connect timeout, missing ``mcp`` extra, handshake error).
    The error string is a short class-name-based summary — it never carries an env value or a traceback.

    ``ok`` answers "does this server work". ``reaches_agent`` answers the question the person
    clicking Test is actually asking — "can the agent use it" — and the two are not the same. See
    :func:`_reach`.
    """
    servers = load_servers(_mcp_path(home))
    cfg = next((s for s in servers if s.name == name), None)
    if cfg is None:
        return {"ok": False, "tools": [], "error": "no such server", **_reach(name, home)}
    try:
        tools = _live_test(cfg)
    except Exception as exc:  # noqa: BLE001 — every failure becomes a short, secret-free error
        _log.warning("MCP test for %r failed: %s", name, type(exc).__name__)
        _remember(home, cfg, ok=False, tool_count=0)
        return {"ok": False, "tools": [], "error": _short_error(exc), **_reach(name, home)}
    _remember(home, cfg, ok=True, tool_count=len(tools))
    annotated = [{**tool, "cues": selection_cues(tool.get("description", ""))} for tool in tools]
    return {"ok": True, "tools": annotated, "error": None, **_reach(name, home)}


def _reach(name: str, home: Path | None = None) -> dict[str, Any]:
    """Whether a run started right now would receive this server's tools, and if not, why not.

    A live connect proves the server works. It does not prove the *agent* can reach it, and the gap
    between those two facts was measured: a server was registered, Test answered "ok, 4 tools" and
    listed all four, and the next run made twenty-two tool calls over nineteen minutes without one
    of them being from the server — because ``mcp_autoload`` is off by default. Nothing on the
    screen said so. "It works" and "you can use it" looked identical, and only one was true.

    Three states, and each has a different remedy:

    * autoload off — nothing reaches any run. Turn it on; no relaunch needed, because the pool is
      built lazily on the first turn that asks for it.
    * autoload on, pool not built yet — the next run builds it and picks this server up.
    * autoload on, pool already built without this name — this server was added after the servers
      were connected, and the pool is deliberately never rebuilt in a process (see
      :mod:`chimera.integrations.mcp_pool`). It needs a relaunch.

    The reason is an ENUM, not a sentence. The app is translated into ten languages, and a screen
    that prints an English string from the server is a screen where one line is in the wrong
    language — the same rule the orchestration frames already follow for ``fell_back.reason``.

    Reads the pool WITHOUT building it, so asking the question never has the side effect of
    answering it — clicking Test must not spawn a subprocess per configured server.

    A fourth state outranks all three: the server's tools changed since the owner approved them, so
    its mount is held (``manifest_held``) whatever autoload says. Turning autoload on would not
    deliver it, and saying "turn autoload on" would send the owner to the wrong remedy.
    """
    if home is not None and held_change(_mcp_path(home), name) is not None:
        return {"reaches_agent": False, "reaches_agent_reason": "manifest_held"}
    from chimera.config import get_settings
    from chimera.integrations.mcp_pool import pool_state

    try:
        estado = pool_state(get_settings())
    except Exception as exc:  # noqa: BLE001 -- a status read must never fail a test
        _log.debug("MCP reach unknown: %s", type(exc).__name__)
        return {"reaches_agent": None, "reaches_agent_reason": None}

    if not estado.autoload:
        return {"reaches_agent": False, "reaches_agent_reason": "autoload_off"}
    if estado.connected is None or name in estado.connected:
        return {"reaches_agent": True, "reaches_agent_reason": None}
    return {"reaches_agent": False, "reaches_agent_reason": "added_after_connect"}


def _short_error(exc: Exception) -> str:
    """A short, secret-free failure message: the exception's own text if it's brief and clean, else its
    class name. Guards against an env value or a long traceback-like string leaking into the UI."""
    text = str(exc).strip()
    if text and len(text) <= 200 and "\n" not in text:
        return text
    return type(exc).__name__
