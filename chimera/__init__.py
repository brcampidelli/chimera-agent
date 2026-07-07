"""Chimera — a self-evolving AI agent with an LLM-Fusion reasoning core.

See the architecture overview in the project README. The package is organized
into focused subsystems (core, fusion, memory, skills, tools, integrations,
scheduler, migration, evolution, governance, orchestration, providers, sandbox,
eval) plus the CLI/TUI/server interfaces.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Single source of truth: the installed package metadata (pyproject `version`),
    # so `chimera version` / doctor / the A2A card never drift from the release.
    __version__ = _pkg_version("chimera-agent")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+source"

__all__ = ["__version__"]
