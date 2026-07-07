"""Diff-gated evolution acceptance — certify a step by its real working-tree diff, never self-report.

nanobot's "Dream" consolidation loop only advances when the actual working-tree *diff* shows a
productive edit, and it builds its audit record from that machine-derived diff — never from the
model's narrative ("never advance the cursor on LLM self-report"). This module is the Chimera
primitive for that discipline.

It works over the same :class:`~chimera.core.checkpoint.FileSnapshot` the verify-or-revert guard
already takes before each attempt, so it needs no git and no extra capture: diff two snapshots,
classify the change, and report a machine-derived summary. The evolution loop can then refuse to
count a "success" that changed nothing — a hollow, self-reported pass with no artifact behind it —
as a training target, and log what actually changed rather than what the model *said* changed.

Normalization ignores whitespace-only churn (trailing spaces, blank-line padding) so a reformat with
no real content change does not read as productive; an added empty file does not either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.core.checkpoint import FileSnapshot


def _normalize(text: str) -> str:
    """Strip trailing whitespace per line and leading/trailing blank lines.

    So a diff that only reflows whitespace (a formatter pass, blank-line padding) does not
    register as a productive content change.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


@dataclass
class ProductiveDiff:
    """The machine-derived classification of what changed between two workspace snapshots."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    @property
    def is_productive(self) -> bool:
        """True iff a real content change happened — the honest signal, not a model's claim."""
        return bool(self.added or self.removed or self.modified)

    @property
    def changed(self) -> list[str]:
        """All touched paths, de-duplicated and sorted — for a compact audit trail."""
        return sorted({*self.added, *self.removed, *self.modified})

    def audit_summary(self, *, max_files: int = 5) -> str:
        """A machine-derived one-line summary (counts + a capped file list). Never a narrative."""
        if not self.is_productive:
            return "diff: no productive change"
        files = self.changed
        shown = ", ".join(files[:max_files])
        more = f", +{len(files) - max_files} more" if len(files) > max_files else ""
        return (
            f"diff: +{len(self.added)} new, ~{len(self.modified)} changed, "
            f"-{len(self.removed)} removed ({shown}{more})"
        )


def diff_snapshots(before: FileSnapshot, after: FileSnapshot) -> ProductiveDiff:
    """Classify the change between two :class:`FileSnapshot`s as a :class:`ProductiveDiff`.

    - *added*: a path present only in ``after`` — productive unless it is a text file whose
      normalized content is empty (a touched empty file is not a real edit).
    - *removed*: a path present only in ``before`` — a deletion is always a real change.
    - *modified*: a text file present in both whose *normalized* content differs.

    Binary files (tracked for presence but not content) count for add/remove but never as
    modified, since their content cannot be compared here.
    """
    added: list[str] = []
    for rel in sorted(after.present - before.present):
        content = after.files.get(rel)
        if content is not None and _normalize(content) == "":
            continue  # a touched empty text file is not a productive edit
        added.append(rel)

    removed = sorted(before.present - after.present)

    modified: list[str] = []
    for rel in sorted(after.present & before.present):
        before_content = before.files.get(rel)
        after_content = after.files.get(rel)
        if before_content is None or after_content is None:
            continue  # binary/unreadable on either side: cannot judge a content change
        if _normalize(before_content) != _normalize(after_content):
            modified.append(rel)

    return ProductiveDiff(added=added, removed=removed, modified=modified)
