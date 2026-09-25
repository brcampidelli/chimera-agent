"""The prompt registry: every prompt the product sends, with its layer, situations and evidence.

See :mod:`chimera.prompts.registry` and `bench/PLAN-study25-system-prompts.md`.
"""

from chimera.prompts.registry import NOT_PROMPTS, SECTIONS, SITUATIONS, PromptSection, by_id

__all__ = ["NOT_PROMPTS", "SECTIONS", "SITUATIONS", "PromptSection", "by_id"]
