"""Self-evolution engine: multi-level (memory/skill/model), verify-or-revert.

Includes the experience buffer (failures as negative examples). The core of attacking
continuous-evolution degradation. The buffer ships in M3; the full engine in M4.
"""

from chimera.evolution.experience import Experience, ExperienceBuffer

__all__ = ["Experience", "ExperienceBuffer"]
