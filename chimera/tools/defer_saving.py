"""What deferral would save on THIS install, both halves, from one function the app and the CLI share.

``chimera.tools.defer.describe_saving`` and ``chimera.integrations.mcp_defer.describe_saving`` were
written so the saving would be measured on the machine that pays for it — ``config.py`` says so
beside both switches — and nothing outside the tests called either one. A promise that a number is
"reported on your installation" with no screen and no command that reports it is a promise only the
source can keep. This is the one place both are called, for ``GET /api/tools/defer-saving`` and
``chimera tools --defer-saving``.

The figure can be a LOSS, and it is reported as one: below a handful of tools the three proxies cost
more than what they replace. A negative ``saving_pct`` is the honest answer, not an error.

What this does NOT measure is the half that matters for quality — whether a model that must look a
tool up still finds it. ``bench/tool_defer/RESULT.md`` tried for the built-in half and was
inconclusive (McNemar p = 0.125); nothing has tried for MCP. The screen says both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from chimera.config import Settings
from chimera.telemetry import get_logger

_log = get_logger("tools.defer_saving")

#: Why there is, or is not, an MCP figure. Distinct values because they ask different things of the
#: owner: "turn autoload on", "start a conversation first", "there is nothing to defer", and
#: "a connected server did not answer the listing".
McpState = Literal["measured", "autoload_off", "not_connected", "no_servers", "unavailable"]


def _half(raw: dict[str, int], deferred: int) -> dict[str, Any]:
    declared, after = raw["declared_chars"], raw["deferred_chars"]
    # Positive is a saving, negative a loss. Rounded to one decimal: the input is a character count
    # standing in for tokens, and a second decimal would claim a precision the proxy does not have.
    pct = round((declared - after) / declared * 100, 1) if declared else 0.0
    return {
        "tools": raw["tools"],
        "deferred": deferred,
        "declared_chars": declared,
        "deferred_chars": after,
        "saving_pct": pct,
    }


def builtin_half(
    settings: Settings, workspace: Path, *, surface_denials: list[str] | None = None
) -> dict[str, Any]:
    """The built-in half, on the registry this deployment would actually declare.

    Fenced first, because a tool the owner denied is declared in neither shape and counting it would
    credit deferral with a saving the denylist already made.

    ``surface_denials`` is what the measured SURFACE removes on top of the deployment's fence. The
    app's chat is the one that does — ``guard_chat_registry`` takes the three execution tools — and
    it is the surface the Settings screen sits on; measured without it, the number counted
    ``run_shell`` as declared on a conversation that never has it.
    """
    from chimera.api.posture import deployment_fence
    from chimera.governance.allowlist import restrict_registry
    from chimera.tools.builtin import default_registry
    from chimera.tools.defer import describe_saving

    registry = default_registry(workspace)
    denied, allowed = deployment_fence(settings)
    denied = denied | frozenset(surface_denials or ())
    if denied or allowed is not None:
        registry = restrict_registry(
            registry, allow=sorted(allowed) if allowed is not None else None, deny=sorted(denied)
        )
    raw = describe_saving(registry)
    return _half(raw, raw["deferred"])


def mcp_half(pool: Any) -> dict[str, Any]:
    from chimera.integrations.mcp_defer import describe_saving

    raw = describe_saving(pool)
    return _half(raw, raw["tools"])


def app_pool(settings: Settings) -> tuple[Any, McpState]:
    """The app's pool, read WITHOUT building it — a status read must not spawn servers."""
    from chimera.integrations import mcp_pool

    state = mcp_pool.pool_state(settings)
    if not state.autoload:
        return None, "autoload_off"
    pool = mcp_pool.built_pool()
    if pool is not None:
        return pool, "measured"
    return None, "no_servers" if state.built else "not_connected"


def saving_report(
    settings: Settings,
    workspace: Path,
    *,
    pool: Any,
    mcp_state: McpState,
    surface_denials: list[str] | None = None,
) -> dict[str, Any]:
    """Both halves, and why the MCP one is absent when it is.

    The MCP half is measured by asking each connected server for its tool list, live — an RPC with
    a ten-second timeout per server — so it can raise, and a server that hung after connecting does.
    That failure is the MCP half's own: it used to escape as a 500, which took the BUILT-IN figure
    down with it (both notes on the screen share one request) and left the owner a "could not
    measure" under a switch whose number had nothing to do with MCP. ``pool_state`` beside it makes
    the same promise for the same reason: a broken pool must not break a status read.
    """
    mcp: dict[str, Any] | None = None
    state = mcp_state
    if pool is not None and mcp_state == "measured":
        try:
            mcp = mcp_half(pool)
        except Exception as exc:  # any server failure; the built-in half must still be reported
            _log.warning("could not measure the MCP half of the deferral saving: %s", exc)
            state = "unavailable"
    return {
        "builtin": builtin_half(settings, workspace, surface_denials=surface_denials),
        "mcp": mcp,
        "mcp_state": state,
    }
