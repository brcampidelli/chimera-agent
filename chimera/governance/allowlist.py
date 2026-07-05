"""Per-session tool allowlist — restrict which tools an agent may use this run.

zoharel's point (r/AI_Agents): every capability should be explicitly allowed on a
per-session basis, so a session only ever holds the tools it actually needs. This
filters a registry down to an allowed set — disallowed tools are **dropped**, not
just gated: they never reach the model's schema, so the agent cannot invoke (or even
be tempted by) what it was not granted. Composes with :func:`govern_registry`:
restrict the session's grant first, then govern whatever survives.
"""

from __future__ import annotations

from collections.abc import Iterable

from chimera.governance.audit import AuditLog
from chimera.telemetry import get_logger
from chimera.tools.registry import ToolRegistry

_log = get_logger("governance.allowlist")


def restrict_registry(
    registry: ToolRegistry,
    *,
    allow: Iterable[str] | None = None,
    deny: Iterable[str] | None = None,
    audit: AuditLog | None = None,
) -> ToolRegistry:
    """Return a new registry holding only the tools this session is allowed to use.

    ``allow=None`` keeps every tool (no allowlist in force); an explicit iterable —
    *including an empty one* — is an allowlist, so ``allow=[]`` grants nothing (a
    fully locked session). ``deny`` removes names even when allowed (deny wins over
    allow). Names not present in the registry are ignored. When an ``audit`` log is
    given and anything is excluded, the decision is recorded for the trail.
    """
    allow_set = None if allow is None else {name.strip() for name in allow if name.strip()}
    deny_set = {name.strip() for name in (deny or ()) if name.strip()}

    kept = ToolRegistry()
    excluded: list[str] = []
    for tool in registry.tools():
        permitted = (allow_set is None or tool.name in allow_set) and tool.name not in deny_set
        if permitted:
            kept.register(tool)
        else:
            excluded.append(tool.name)

    if excluded:
        _log.debug(
            "session allowlist excluded %d tool(s): %s",
            len(excluded),
            ", ".join(sorted(excluded)),
        )
        if audit is not None:
            audit.record(
                "tool_allowlist",
                {
                    "allow": sorted(allow_set) if allow_set is not None else None,
                    "deny": sorted(deny_set),
                    "excluded": sorted(excluded),
                    "kept": sorted(kept.names()),
                },
            )
    return kept
