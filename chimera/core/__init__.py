"""Core agent loop: ReAct, planner, persistable state machine, verify-or-revert.

State is kept *outside* the LLM context (transcript + git + DB) to resist
continuous-evolution degradation. The minimal tool-calling loop lands in M1; the
planner and verify-or-revert mature in M3.
"""

from chimera.core.agent import (
    DEFAULT_SYSTEM_PROMPT,
    Agent,
    AgentConfig,
    AgentResult,
)
from chimera.providers import SupportsComplete

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentResult",
    "SupportsComplete",
    "DEFAULT_SYSTEM_PROMPT",
]
