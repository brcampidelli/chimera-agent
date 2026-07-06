"""LLM-Fusion engine: panel -> judge -> synthesizer, plus a cost-aware router.

The differentiator. The lift comes from the synthesis step itself, not only from
model diversity.
"""

from chimera.fusion.consistency import SelfConsistency, majority
from chimera.fusion.engine import (
    FusionConfig,
    FusionEngine,
    FusionTrace,
    PanelResponse,
    StageUsage,
)
from chimera.fusion.router import EscalationVerifier, RoutedBackend, RoutingPolicy
from chimera.fusion.verifier_select import Scorer, Selection, VerifierSelector, llm_scorer

__all__ = [
    "FusionEngine",
    "FusionConfig",
    "FusionTrace",
    "PanelResponse",
    "StageUsage",
    "RoutedBackend",
    "RoutingPolicy",
    "EscalationVerifier",
    "SelfConsistency",
    "majority",
    "VerifierSelector",
    "Selection",
    "Scorer",
    "llm_scorer",
]
