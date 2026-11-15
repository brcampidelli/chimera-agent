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
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

_log = get_logger("governance.allowlist")

#: Tools that run code the model wrote, on whatever machine the session is on.
#:
#: An allowlist naming one of these does not bound the session to the names in the list. It bounds
#: which *tool* the model calls to reach everything else — `code_interpreter` can open a socket,
#: `execute_code` can read a file the list excluded, and either can do what `run_shell` was removed
#: to prevent. What still bounds the session is ``CHIMERA_HOST_EXEC`` and the sandbox; this list
#: does not.
#:
#: The project already knew this about the host-exec gate and said so in
#: ``chimera/tools/code.py``: `code_interpreter` honours ``CHIMERA_HOST_EXEC`` unconditionally
#: "because otherwise ``deny`` would be trivially bypassable — the model would simply pick this tool
#: over ``run_shell``". The same sentence is true of the allowlist and nothing said it, which is the
#: shape arXiv 2609.07360 measured across 3,171 repositories: 16.0% carry a grant that reads as a
#: restriction and is not, `Bash(python:*)` being the canonical one.
ARBITRARY_CODE = frozenset({"run_shell", "code_interpreter", "execute_code"})


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
    # The owner's deployment policy joins the deny side and only that side: its later `allow`
    # undoes an earlier deny of policy.json and nothing else, so it can never put back a name
    # `deny` or a missing `allow` removed here (`chimera.deployment_policy`, the golden rule).
    from chimera.deployment_policy import tool_denied

    owner_denied = {tool.name for tool in registry.tools() if tool_denied(tool.name)}

    kept = ToolRegistry.like(registry)
    excluded: list[str] = []
    for tool in registry.tools():
        permitted = (
            (allow_set is None or tool.name in allow_set)
            and tool.name not in deny_set
            and tool.name not in owner_denied
        )
        if permitted:
            kept.register(tool)
        else:
            excluded.append(tool.name)

    if _keep_mcp_describe_reachable(registry, kept, deny_set | owner_denied) and "mcp_describe" in excluded:
        # Put back on purpose (see the helper): it was not excluded, and the audit must not say so.
        excluded.remove("mcp_describe")

    # Said once, where the list is applied, because this is the only place that knows an allowlist
    # is in force AND which tools it kept. WARNING rather than debug: the whole value of an
    # allowlist is the belief that it bounds the session, and a list that does not is a belief
    # someone is acting on. See :data:`ARBITRARY_CODE`. Read off the tools KEPT, not the names the
    # list spells: a list naming `code_interpreter` on a registry that has none keeps nothing that
    # runs code, and warning about it was a false alarm (study 25's /review bench found it).
    kept_names = {tool.name for tool in kept.tools()}
    escapes = sorted(kept_names & ARBITRARY_CODE) if allow_set is not None else []
    if escapes:
        _log.warning(
            "the tool allowlist keeps %s, which run code this session wrote — so the list bounds "
            "which tool is called, not what the session can do. What still bounds it is "
            "CHIMERA_HOST_EXEC and the sandbox. Remove %s from the allowlist if the list was meant "
            "to be the boundary.",
            ", ".join(escapes),
            " / ".join(escapes),
        )

    if excluded:
        _log.debug(
            "session allowlist excluded %d tool(s): %s",
            len(excluded),
            ", ".join(sorted(excluded)),
        )
    # `or escapes`: an allowlist that excluded nothing still wrote no trail, and "a list was in
    # force and it bounded nothing" is precisely the line a governance log exists to carry. The
    # entry is what a later reader has instead of the warning, which lives only in a log nobody
    # keeps.
    if (excluded or escapes) and audit is not None:
        audit.record(
            "tool_allowlist",
            {
                "allow": sorted(allow_set) if allow_set is not None else None,
                "deny": sorted(deny_set),
                "excluded": sorted(excluded),
                "kept": sorted(kept.names()),
                "arbitrary_code_kept": escapes,
            },
        )
    return kept


def _wrapper_chain(tool: Tool) -> list[Tool]:
    """``tool`` and what it wraps, outermost first: the ledger, governance and other wrappers all keep
    the tool they run in ``inner``."""
    chain = [tool]
    while isinstance(getattr(chain[-1], "inner", None), Tool):
        chain.append(chain[-1].inner)  # type: ignore[attr-defined]
    return chain


def _keep_mcp_describe_reachable(
    source: ToolRegistry, kept: ToolRegistry, denied: set[str]
) -> bool:
    """Point the declared ``mcp_describe`` at the registry this filter produced (P0-16).

    That tool answers for the tools of the registry it was built against, so after a filter it would
    describe tools the session, the project (`tools_deny`) or the deployment removed. A copy is made
    over ``kept``: it describes only tools that survived, and only those whose description was cut.
    The shared original is never touched, because a parent run still uses it.

    The tool may be wrapped (`ledger_registry`, `govern_registry`), in either order with this filter.
    The wrappers are walked through ``inner``, the one place they keep what they run, and the SAME
    layers are shallow-copied around the rebuilt tool, so the ledger and governance protections stay
    on it exactly as they were.

    **Chosen: implicitly allowed.** Under an allowlist the cut descriptions of the ALLOWED tools have
    no other road to their remainder, and the owner who allowed ``srv_read_it`` allowed the tool,
    description and all. It is added only when at least one kept tool is cut and never over an
    explicit deny of ``mcp_describe``; it grants nothing beyond text the kept tools already carry,
    so it is not a way to reach a tool the list left out. (The alternative, switching the allowed
    servers to ``full`` automatically, would put the unfenced text back in the tool list.)

    Returns whether ``mcp_describe`` is now present in ``kept`` because of this function.
    """
    import copy

    from chimera.integrations.mcp_defer import DeclaredDescribeTool

    if "mcp_describe" not in source or "mcp_describe" in denied:
        return False
    chain = _wrapper_chain(source.get("mcp_describe"))
    original = chain[-1]
    if not isinstance(original, DeclaredDescribeTool):
        return False
    rebuilt = original.bound_to_registry(kept) or DeclaredDescribeTool([], kept)
    had_cut = bool(rebuilt.catalogue.by_name)
    if not had_cut and "mcp_describe" not in kept:
        return False
    outer: Tool = rebuilt
    for layer in reversed(chain[:-1]):
        clone = copy.copy(layer)
        clone.inner = outer  # type: ignore[attr-defined]
        outer = clone
    kept.register(outer, replace=True)
    return had_cut
