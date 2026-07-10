"""Bug-report task normalizer (arXiv 2607.07593).

A rambling bug report *hurts* an agent: long narrative buries the few facts that matter (where, how to
reproduce, expected vs actual, a fix hint) and the paper measured that trimming it — surfacing the
salient fields up front — improves resolution. This is a deterministic, model-free normalizer that runs
BEFORE planning: when a task looks like a bug report and is long enough to ramble, it extracts the
salient lines into a concise structured header and caps the original narrative, so the planner and
worker see the structure first.

Conservative and lossless-enough by design:

- A task that doesn't look like a bug report, or is already short, is returned **unchanged**.
- The extracted header is *prepended*; the original report is kept but **trimmed** to a cap (so a long
  ramble is bounded, never fully dropped — a short bug report is left intact).
- It normalizes only the text fed to the planner/worker prompt; the raw task stays the identity used
  for memory keys and the experience buffer (see :class:`~chimera.core.autonomous.AutonomousAgent`).
"""

from __future__ import annotations

import re

_MIN_LEN = 400  # below this a task is too short to ramble — leave it alone
_KEEP_CHARS = 600  # cap on the retained original narrative

# A task looks like a bug report if it trips any of these.
_BUG_TRIGGERS = re.compile(
    r"\b(?:bug|\w*error|\w*exception|traceback|stack ?trace|fails?|failing|broken|crash|"
    r"does(?:n't| not) work|not working|expected|regression)\b",
    re.I,
)

# File/location references (searched over the whole text — a path needs no sentence context).
_FILE_RE = re.compile(
    r"\b[\w./\\-]*[\w-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|c|cpp|h|hpp|md|json|ya?ml|toml|sql|sh)\b(?::\d+)?"
)
# Salient-field keyword matchers, applied per SENTENCE (not per line) — a single-paragraph bug report
# has no newlines, so line matching would capture the whole blob and truncate away the facts.
_ERROR_RE = re.compile(r"(?i)\b(?:traceback|\w*error|\w*exception)\b")
_EXPECTED_RE = re.compile(r"(?i)\b(?:expected|actual|should (?:be|return|equal)|but (?:got|returns?|is))\b")
_REPRO_RE = re.compile(
    r"(?i)(?:^\s*\d+[.)]\s|\b(?:steps? to reproduce|to reproduce|reproduce|repro)\b|"
    r"\brun\s+(?:pytest|python3?|npm|pnpm|yarn|node|chimera|make|\.?/[\w./-]+|[\w./-]+\.(?:py|sh|js|ts)))"
)
_FIX_RE = re.compile(r"(?i)\b(?:fix|should be|instead of|root cause|the (?:bug|issue) is)\b")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def looks_like_bug_report(task: str) -> bool:
    """True when the task trips a bug-report trigger word."""
    return bool(_BUG_TRIGGERS.search(task))


def _sentences(text: str) -> list[str]:
    """Split into sentences/lines so per-field matching sees short, salient units."""
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _dedup(items: list[str], *, limit: int, maxlen: int = 200) -> list[str]:
    """First ``limit`` unique, stripped, bounded-length items (order-preserving)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        item = raw.strip()[:maxlen]
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
            if len(out) >= limit:
                break
    return out


def normalize_task(task: str) -> str:
    """Reshape a long bug-report task into a structured header + trimmed narrative.

    Returns the task unchanged when it doesn't look like a bug report or is too short to ramble.
    """
    if len(task) < _MIN_LEN or not looks_like_bug_report(task):
        return task

    sentences = _sentences(task)
    sections: list[str] = []
    locations = _dedup(_FILE_RE.findall(task), limit=6, maxlen=80)
    if locations:
        sections.append(f"- Location: {', '.join(locations)}")
    errors = _dedup([s for s in sentences if _ERROR_RE.search(s)], limit=3)
    if errors:
        sections.append("- Error:\n" + "\n".join(f"    {e}" for e in errors))
    expected = _dedup([s for s in sentences if _EXPECTED_RE.search(s)], limit=3)
    if expected:
        sections.append("- Expected vs actual:\n" + "\n".join(f"    {e}" for e in expected))
    repro = _dedup([s for s in sentences if _REPRO_RE.search(s)], limit=6)
    if repro:
        sections.append("- Reproduce:\n" + "\n".join(f"    {r}" for r in repro))
    fixes = _dedup([s for s in sentences if _FIX_RE.search(s)], limit=3)
    if fixes:
        sections.append("- Fix hint:\n" + "\n".join(f"    {f}" for f in fixes))

    if not sections:
        return task  # nothing salient extracted — don't trim blindly

    trimmed = task[:_KEEP_CHARS].rstrip()
    if len(task) > _KEEP_CHARS:
        trimmed += "\n… [original report trimmed — the salient facts are above]"
    return (
        "Normalized bug report (salient facts first):\n"
        + "\n".join(sections)
        + "\n\nOriginal report:\n"
        + trimmed
    )
