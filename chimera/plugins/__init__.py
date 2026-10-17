"""Claude Code plugins, read as data: the manifest, the component inventory, and marketplaces.

This package only READS and VALIDATES. It installs nothing, runs nothing, imports nothing from a
plugin and touches no network; a later step decides what, if anything, is installed and switched on
(ESTUDO-01 §6.3: third-party plugins start pending, hooks/monitors/bin stay inert, MCP goes through
`chimera.integrations.mcp_pins`).
"""

from chimera.plugins.manifest import read_plugin
from chimera.plugins.marketplace import classify_source, parse_marketplace, read_marketplace
from chimera.plugins.model import (
    Author,
    Capability,
    CapabilitySummary,
    Code,
    Component,
    ComponentKind,
    MarketplaceEntry,
    MarketplaceReport,
    PluginManifest,
    PluginReport,
    PluginSource,
    Problem,
    Severity,
    SourceKind,
    UserConfigOption,
)

__all__ = [
    "Author",
    "Capability",
    "CapabilitySummary",
    "Code",
    "Component",
    "ComponentKind",
    "MarketplaceEntry",
    "MarketplaceReport",
    "PluginManifest",
    "PluginReport",
    "PluginSource",
    "Problem",
    "Severity",
    "SourceKind",
    "UserConfigOption",
    "classify_source",
    "parse_marketplace",
    "read_marketplace",
    "read_plugin",
]
