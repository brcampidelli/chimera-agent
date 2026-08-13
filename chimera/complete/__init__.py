"""Inline completion: the grey text that appears ahead of the cursor."""

from chimera.complete.inline import Completion, complete_inline, reset_slots
from chimera.complete.outcomes import acceptance, record_outcome, record_shown

__all__ = [
    "Completion",
    "acceptance",
    "complete_inline",
    "record_outcome",
    "record_shown",
    "reset_slots",
]
