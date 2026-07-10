"""Cheap trace anti-pattern detectors (TraceProbe, arXiv 2607.06184).

An outcome-only signal ("did verify pass?") never says *why* a hard attempt went wrong. TraceProbe
scans the per-step tool trace for cheap, auditable anti-patterns and turns them into an advisory
**retry hint** — a side-signal for the fitness gate, never a hard block. Two detectors (the two the
paper found carry most of the signal):

- **search-loop** — the agent kept exploring (read / search / list / fetch) without ever acting
  (write / edit) or checking, i.e. spinning instead of making progress.
- **verification-skip** — the agent changed files (write / edit) but ran nothing to confirm the change
  (no test / verify / shell), i.e. moved on without checking its own work.

Both operate on the ordered ``{tool, ok}`` events from
:func:`chimera.ecosystem.events.events_from_transcript`, so they are deterministic and testable
without a model. They are wired **only into the failure retry-feedback** path in the autonomous loop:
on an attempt that already failed the external verify-or-revert gate, "you searched a lot without
editing" / "you edited without checking" is fair coaching for the next attempt. They never decide
success — the executable verifier remains ground truth.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Tool-name markers by role. Write/check take precedence over read (an ``apply_patch`` is a write,
# not a read, even though it "reads" a file to patch it).
_WRITE_MARKERS = ("write", "edit", "patch", "apply", "create", "append", "insert")
_CHECK_MARKERS = ("test", "verify", "run", "shell", "exec", "check", "lint", "pytest", "build")
_READ_MARKERS = ("read", "list", "glob", "grep", "search", "find", "fetch", "http", "browse", "view", "cat")

DEFAULT_SEARCH_RUN = 4  # consecutive read/search steps (no write/check between) that flag a search-loop


@dataclass
class AntiPattern:
    kind: str  # "search-loop" | "verification-skip"
    detail: str


def _category(tool: str) -> str:
    name = tool.lower()
    if any(m in name for m in _WRITE_MARKERS):
        return "write"
    if any(m in name for m in _CHECK_MARKERS):
        return "check"
    if any(m in name for m in _READ_MARKERS):
        return "read"
    return "other"


def detect_anti_patterns(
    events: Sequence[dict[str, Any]], *, search_run: int = DEFAULT_SEARCH_RUN
) -> list[AntiPattern]:
    """Detect trace anti-patterns over the ordered ``{tool, ok}`` events. Empty list if none."""
    cats = [_category(str(e.get("tool", "?"))) for e in events]
    found: list[AntiPattern] = []

    # search-loop: the longest run of consecutive 'read' steps, broken by a write or check ('other'
    # steps are neutral — they neither extend nor reset the run).
    longest = run = 0
    for cat in cats:
        if cat == "read":
            run += 1
            longest = max(longest, run)
        elif cat in ("write", "check"):
            run = 0
    if longest >= search_run:
        found.append(
            AntiPattern(
                "search-loop",
                f"{longest} consecutive search/read steps with no edit or check in between",
            )
        )

    # verification-skip: edited something but never ran a test/verify/command to confirm it.
    if any(cat == "write" for cat in cats) and not any(cat == "check" for cat in cats):
        found.append(
            AntiPattern(
                "verification-skip",
                "edited files but ran no test/verify/command to confirm the change",
            )
        )
    return found


def anti_pattern_hint(events: Sequence[dict[str, Any]]) -> str:
    """Format detected anti-patterns as an advisory retry-feedback line, or ``""`` if none."""
    patterns = detect_anti_patterns(events)
    if not patterns:
        return ""
    lines = "\n".join(f"- {p.kind}: {p.detail}" for p in patterns)
    return f"Process check — the last attempt showed these anti-patterns; avoid them next time:\n{lines}"
