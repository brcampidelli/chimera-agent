"""Select and wrap catalogued tools that execute code or change a target, for owner alerts and audit.

The rea tool list follows rea-agents 6.4.0's TOOL_EFFECTS entries marked ``executesForeignCode``,
``mutatesDevice`` or ``mutatesTarget``. Any other catalogue entry that declares ``exec_tools`` is
selected the same way (VoiceStudio's voice-profile creators are the second case). These tools remain
available; this module does not enforce read-only restrictions.

A configured server is matched to its catalogue entry by name, by the package token in its command
line, or by its URL: a local HTTP server that the user named differently is still the same service.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from chimera.config import Settings
from chimera.integrations.mcp_catalog import CATALOG
from chimera.integrations.mcp_config import load_servers
from chimera.tools.base import Tool, is_refusal
from chimera.tools.registry import ToolRegistry

_log = logging.getLogger(__name__)

# The rea event name is part of the documented audit trail (docs/rea.md), so rea keeps it. Every
# other catalogue entry records the neutral name, with its catalogue id in the payload.
_REA_EVENT = "rea_tool_executed"
_NEUTRAL_EVENT = "exec_tool_executed"

REA_WRITE_TOOLS: frozenset[str] = frozenset(next(e.exec_tools for e in CATALOG if e.id == "rea"))


def _exec_bindings(settings: Settings) -> dict[str, str]:
    """Map each configured tool name that executes code or changes a target to its catalogue id."""
    servers = load_servers(settings.home / "mcp.json")
    bindings: dict[str, str] = {}
    for cfg in servers:
        command_line = f"{cfg.command} {' '.join(cfg.args)}"
        for entry in CATALOG:
            if not entry.exec_tools:
                continue
            matched = (
                entry.id == cfg.name
                or bool(entry.match and entry.match in command_line)
                or bool(entry.url and cfg.url and cfg.url == entry.url)
            )
            if matched:
                for tool in entry.exec_tools:
                    bindings[f"{cfg.name}_{tool}"] = entry.id
    return bindings


def exec_alert_names(settings: Settings) -> list[str]:
    """Return names of configured tools that execute code or mutate a target, in any catalogue entry."""
    return sorted(_exec_bindings(settings))


#: Compatibility name: the selection began as rea-only and existing callers and tests use it.
rea_alert_names = exec_alert_names


class ExecAlertTool(Tool):
    """Execute a catalogued tool that changes a target, and report each invocation to the owner."""

    def __init__(
        self,
        inner: Tool,
        *,
        surface: str,
        audit: Any,
        alert: Callable[[str], None] | None,
        catalog_id: str = "rea",
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters
        self.open_arguments = getattr(inner, "open_arguments", False)
        self.untrusted_output = getattr(inner, "untrusted_output", False)
        self._surface = surface
        self._audit = audit
        self._alert = alert
        self._catalog_id = catalog_id

    def __getattr__(self, name: str) -> Any:
        """Expose optional tool capabilities (approval hooks, reach settings) to outer layers."""
        return getattr(self.inner, name)

    def run(self, **kwargs: Any) -> str:
        """Run the original tool; the alert is additional and never changes its result."""
        result = self.inner.run(**kwargs)
        if is_refusal(result):
            return result

        at = datetime.now(UTC).isoformat()
        payload = {"tool": self.name, "surface": self._surface, "at": at, "catalog_id": self._catalog_id}
        event = _REA_EVENT if self._catalog_id == "rea" else _NEUTRAL_EVENT
        try:
            self._audit.record(event, payload)
        except Exception:  # noqa: BLE001 — audit failure must not change tool behavior
            _log.exception("%s audit failed for %s on %s", event, self.name, self._surface)
        if self._alert is not None:
            try:
                self._alert(
                    f"Chimera ran {self._catalog_id} tool {self.name} on {self._surface} at {at}."
                )
            except Exception:  # noqa: BLE001 — notification failure must not change tool behavior
                _log.warning("tool execution alert failed for %s on %s", self.name, self._surface)
        else:
            _log.warning("%s tool %s ran on %s at %s", self._catalog_id, self.name, self._surface, at)
        return result


#: Compatibility name for the rea-only class; ``ExecAlertTool`` with the rea catalogue id.
ReaAlertTool = ExecAlertTool


def alert_exec_tools(
    registry: ToolRegistry,
    *,
    settings: Settings,
    surface: str,
    audit: Any,
    alert: Callable[[str], None] | None = None,
) -> ToolRegistry:
    """Wrap configured target-changing catalogue tools for per-call audit and owner alerts.

    An explicit ``alert`` is the surface's notice sink (and the test seam). Otherwise the
    configured approval webhook is the existing out-of-band channel; the audit log remains the
    durable in-app record when no webhook is configured. Wrapping is independent of the surface's
    approval capability, so any existing approval or ledger layer remains active around these tools.
    """
    bindings = _exec_bindings(settings)
    tools = registry.tools()
    if not any(tool.name in bindings for tool in tools):
        # Off-mode tests require identity, and an installation without these servers must remain unchanged.
        return registry
    if alert is None:
        from chimera.governance.approval import deliverer_for

        alert = deliverer_for(settings)
    wrapped = ToolRegistry.like(registry)
    for tool in tools:
        if tool.name in bindings:
            wrapped.register(
                ExecAlertTool(
                    tool, surface=surface, audit=audit, alert=alert, catalog_id=bindings[tool.name]
                )
            )
        else:
            wrapped.register(tool)
    return wrapped


#: Compatibility name for the rea-era entry point; the callers in serve.py and profile.py use it.
alert_rea_tools = alert_exec_tools
