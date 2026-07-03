"""LLM-Fusion engine: panel -> judge -> synthesizer, plus a cost-aware router.

The differentiator. The lift comes from the synthesis step itself, not only from
model diversity.
"""

from chimera.fusion.engine import (
    FusionConfig,
    FusionEngine,
    FusionTrace,
    PanelResponse,
    StageUsage,
)
from chimera.fusion.router import RoutedBackend, RoutingPolicy

__all__ = [
    "FusionEngine",
    "FusionConfig",
    "FusionTrace",
    "PanelResponse",
    "StageUsage",
    "RoutedBackend",
    "RoutingPolicy",
]
