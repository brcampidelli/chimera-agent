"""The prompt registry: every prompt the product sends, with its layer, situations and evidence.

See :mod:`chimera.prompts.registry` and `bench/PLAN-study25-system-prompts.md`.
"""

import hashlib

from chimera.prompts.registry import NOT_PROMPTS, SECTIONS, SITUATIONS, PromptSection, by_id


def fingerprint(text: str) -> str:
    """Twelve hex characters of the SHA-256 of ``text``: enough to tell prompt versions apart."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


__all__ = ["NOT_PROMPTS", "SECTIONS", "SITUATIONS", "PromptSection", "by_id", "fingerprint"]
