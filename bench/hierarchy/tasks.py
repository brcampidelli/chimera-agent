"""Task suite for the hierarchy paired A/B — thin shim over the registered design.

The tasks, the baseline prompt and the per-document specs live in
:mod:`chimera.eval.hierarchy_ab` (so the unit tests exercise the same code this
bench runs). This module just re-exports them for the runner.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from chimera.eval.hierarchy_ab import (  # noqa: E402
    HierarchyTask,
    baseline_prompt,
    make_specs,
    synthetic_tasks,
)

__all__ = ["HierarchyTask", "baseline_prompt", "make_specs", "synthetic_tasks"]
