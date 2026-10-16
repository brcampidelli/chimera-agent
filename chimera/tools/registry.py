"""A registry that holds the tools available to an agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger
from chimera.tools.base import Tool, is_refusal, unknown_arguments_error

_log = get_logger("tools.registry")


class ToolNotFoundError(KeyError):
    """Raised when a tool is requested by a name that is not registered."""


class DuplicateToolError(ValueError):
    """Raised when registering a tool whose name is already taken."""


class ToolRegistry:
    """An ordered collection of uniquely-named tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        #: The skill bundles a run built on this registry may be told about, when a project's pack
        #: narrowed them (`chimera.core.project_pack`); ``None`` — every switched-on bundle —
        #: otherwise. Set by the assembly that narrowed the TOOLS from the same pack, so the two
        #: halves of one decision travel together instead of being decided twice from two roots.
        self.bundle_only: frozenset[str] | None = None
        #: The home those bundles are read from, when the assembly ran on settings of its own (an
        #: app built with ``settings=``, a bench arm). ``None``: the process's settings. Stamped for
        #: the same reason as ``bundle_only``: an agent that re-read the PROCESS settings listed
        #: another home's bundles than the screen and the tools it ran with.
        self.bundle_home: Path | None = None

    @classmethod
    def like(cls, source: object) -> ToolRegistry:
        """An empty registry for a run DERIVED from ``source``'s: a role's subset, a subagent's,
        a governed or ledgered wrapping. The tools are the caller's to choose; the skill scope is
        not — a run narrowed further is still a run in the same project, and a wrapper that dropped
        the scope would hand a subagent every bundle the pack kept out of its parent's prompt."""
        out = cls()
        out.bundle_only = getattr(source, "bundle_only", None)
        out.bundle_home = getattr(source, "bundle_home", None)
        return out

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        if tool.name in self._tools and not replace:
            raise DuplicateToolError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool
        _log.debug("registered tool %s", tool.name)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def to_openai_schema(self, *, compact: bool = False) -> list[dict[str, Any]]:
        """Schemas for all tools, to advertise to a model.

        With ``compact=True``, annotation noise is stripped and parameter prose trimmed
        at advertise-time (semantics preserved) to cut the tokens re-sent every step.
        """
        from chimera.deployment_policy import tool_denied

        # A tool the owner's deployment policy denies is not advertised: offering it would only
        # buy a refusal from `run` below and a step spent learning it.
        schemas = [
            tool.to_openai_schema() for tool in self._tools.values() if not tool_denied(tool.name)
        ]
        if compact:
            from chimera.tools.schema_compact import compact_schemas

            return compact_schemas(schemas)
        return schemas

    def run(self, name: str, **kwargs: Any) -> str:
        """Look up and execute a tool by name."""
        from chimera.obs import span

        with span("tool.run", **{"tool.name": name}) as sp:
            tool = self.get(name)
            # The one door every registered tool is executed through, so the owner's deployment
            # policy is enforced here whatever assembled the registry: a surface that skipped the
            # restriction filter because it had no denylist of its own (the Code turn, a governed
            # profile) still cannot run a tool `policy.json` denies.
            from chimera.deployment_policy import tool_denied

            if tool_denied(name):
                from chimera.tools.base import refusal

                refused: str = refusal(
                    f"{name} is denied by the owner's deployment policy (policy.json). Do not "
                    "retry, and do not report this as done."
                )
                sp.set(**{"tool.ok": False, "tool.output_chars": len(refused)})
                return refused
            # Before the tool, and before its wrappers: an argument the schema does not declare is
            # one the tool would have ignored, and a governance gate approving a call that then
            # quietly did something else is not a check worth running.
            out = unknown_arguments_error(tool, kwargs) or tool.run(**kwargs)
            # Same question as `Agent.run` asks, so it must have the same answer — the rule
            # was written out twice and only one copy knew about refusals.
            sp.set(**{
                "tool.ok": not out.startswith("error:") and not is_refusal(out),
                "tool.output_chars": len(out),
            })
            return out
