"""The review card the Code screen's ``/code-review`` shows: a :class:`ReviewReport` turned into what
the card needs, and nothing it does not.

The review itself is :func:`chimera.review.review` unchanged: the same finder prompt, the same anchor
check (a finding about a line the diff does not show is dropped, with that reason written down), the
same cautious verifier. This module decides only three things the terminal does not: how a priority
reads on a card, which findings are shown under the person's limit, and which files a card groups them
under with the raw patch the diff view needs.

A review that did not finish is not a card with no findings: the status says ``incomplete`` and the
notes say why, so a provider outage cannot read as a clean change.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from chimera.governance.sanitize import sanitize_untrusted
from chimera.review.diff import FileDiff, ReviewDiff, parse
from chimera.review.pipeline import BUDGET_REASON, VERIFY_BUDGET_REASON
from chimera.review.report import Finding, ReviewReport

#: How a finder's priority reads on the card. P0 (data loss, a hole, a broken main path) and P1 (a
#: bug ordinary use hits) are "high"; the card's three colours follow.
SEVERITY: dict[str, str] = {"P0": "high", "P1": "high", "P2": "medium", "P3": "low"}

#: Cost ceilings of one review. Past them a part of the change is left out, and the card says so.
MAX_REVIEW_CHARS = 200_000
MAX_REVIEW_BATCHES = 6
MAX_REVIEW_CHECKS = 20
#: Wall-clock limit for the whole review, the reviewer calls included.
REVIEW_TIMEOUT_S = 180.0
#: Per model call, finder and verifier alike: a slow call ends, the rest of the review goes on.
REVIEW_CALL_TIMEOUT_S = 60.0
#: The largest request body the route reads. Checked before it is parsed.
MAX_REVIEW_BODY = 2_000_000
#: The most findings a card shows when the person asks for a number and gives none.
DEFAULT_LIMIT = 10
#: The largest number the person can ask for; ``all`` is the same as asking for this many.
MAX_LIMIT = 200


def parse_limit(value: str | int | None) -> int | None:
    """``--max-findings`` as a count, ``None`` for ``all``, the default when absent.

    Raises ``ValueError`` for anything that is neither ``all`` nor a whole number from 1 to MAX_LIMIT:
    a silent fallback would show fewer findings than the person asked for and say nothing.
    """
    if value is None or value == "":
        return DEFAULT_LIMIT
    if isinstance(value, str) and value.strip().lower() == "all":
        return None
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise ValueError("max_findings takes a number or 'all'") from exc
    if not 1 <= number <= MAX_LIMIT:
        raise ValueError(f"max_findings takes a number from 1 to {MAX_LIMIT}, or 'all'")
    return number


def finding_out(finding: Finding) -> dict[str, Any]:
    return {
        "id": finding.id,
        "priority": finding.priority,
        "severity": SEVERITY.get(finding.priority, "low"),
        "file": finding.file,
        "line": finding.line,
        "title": finding.title,
        "consequence": finding.consequence,
        "evidence": finding.evidence,
        "confidence": finding.confidence,
        "unverified": finding.verdict is not None and finding.verdict.state == "unverified",
    }


def card_status(report: ReviewReport) -> str:
    """``findings``, ``no_findings``, ``incomplete`` or ``empty``, as the report says."""
    return report.status


def build_card(
    report: ReviewReport,
    *,
    limit: int | None,
    patch_of: Callable[[str], str],
    tainted: bool,
    extra_notes: Sequence[str] = (),
    partial: bool = False,
) -> dict[str, Any]:
    """The card's payload for ``report``.

    ``limit`` keeps the first N findings in the report's order (P0 first) and counts the rest as
    ``omitted``. ``patch_of`` gives the raw diff of a file that carries a shown finding; a file with
    no shown finding is not read at all. ``tainted`` is the conversation's taint after this review.

    A review is ``partial`` when a cost ceiling left part of the change out, or a finding went
    unverified: a partial review with no findings is ``incomplete``, never ``no_findings``. A reviewer
    failure is named generically here; the raw reason is for the server log, not the card.
    """
    shown = report.findings if limit is None else report.findings[:limit]
    groups: dict[str, list[dict[str, Any]]] = {}
    for finding in shown:
        groups.setdefault(finding.file, []).append(finding_out(finding))
    files = [
        {"file": path, "patch": patch_of(path), "findings": items}
        for path, items in groups.items()
    ]
    notes = [*extra_notes, *report.notes]
    for item in report.not_reviewed:
        if item.reason == "binary file":
            continue
        if item.reason == BUDGET_REASON:
            notes.append(f"outside this review's budget: {item.file}")
        else:
            notes.append(f"not reviewed: {item.file} (the reviewer could not read it)")
    partial = (
        partial
        or any(item.reason == BUDGET_REASON for item in report.not_reviewed)
        or any(f.verdict is not None and f.verdict.reason == VERIFY_BUDGET_REASON for f in report.findings)
    )
    status = card_status(report)
    if partial and status == "no_findings":
        status = "incomplete"
    usage = report.usage
    return {
        "status": status,
        "partial": partial,
        "base": report.base_label,
        "model": report.reviewer.model,
        "usd": usage.usd,
        "shown": len(shown),
        "omitted": len(report.findings) - len(shown),
        "dropped": len(report.dropped),
        "tainted": tainted,
        "files": files,
        "notes": notes,
        "untested_paths": list(report.untested_paths),
    }

def edits_review(edits: Sequence[tuple[str, str]]) -> tuple[ReviewDiff, dict[str, str], list[str]]:
    """The conversation's own edits as the change to review, for a folder with no git diff.

    ``edits`` are ``(path, unified patch)`` pairs in the order the agent made them: the same record the
    transcript's edit cards show (their ``edit:<turn>:<n>`` origin is what the comments are keyed by).
    Returns the diff, the joined patch of each file, and the paths whose patch could not be read as a
    diff. An unreadable edit is named, never skipped without a word.
    """
    files: dict[str, FileDiff] = {}
    patches: dict[str, list[str]] = {}
    unreadable: list[str] = []
    for path, patch in edits:
        parsed = parse(patch)
        if not parsed:
            unreadable.append(path)
            continue
        for item in parsed:
            if item.path in files:
                files[item.path].hunks.extend(item.hunks)
            else:
                files[item.path] = item
            bucket = patches.setdefault(item.path, [])
            if patch not in bucket:
                bucket.append(patch)
    diff = ReviewDiff(
        list(files.values()), base="", target="conversation edits",
        base_label="edições da conversa",
    )
    return diff, {path: "\n".join(items) for path, items in patches.items()}, unreadable


def sanitised(diff: ReviewDiff) -> ReviewDiff:
    """The diff with control tokens defanged in its headers and lines, before a model reads it.

    A repository, a fork or vendored code is somebody else's text: a line that spells a chat-template
    token must not reach the reviewer as one. Paths are kept as they are, so the patch lookup still matches.
    """
    files = [
        replace(
            item,
            hunks=[
                replace(
                    hunk,
                    header=sanitize_untrusted(hunk.header),
                    lines=[replace(line, text=sanitize_untrusted(line.text)) for line in hunk.lines],
                )
                for hunk in item.hunks
            ],
        )
        for item in diff.files
    ]
    return ReviewDiff(files, base=diff.base, target=diff.target, base_label=diff.base_label)


def fit_diff(diff: ReviewDiff, budget: int) -> tuple[ReviewDiff, list[str]]:
    """The files that fit in ``budget`` rendered characters, in path order; the rest, named."""
    kept: list[FileDiff] = []
    left: list[str] = []
    used = 0
    for item in sorted(diff.files, key=lambda f: f.path):
        size = len("\n".join(item.render()))
        if used + size > budget:
            left.append(item.path)
            continue
        kept.append(item)
        used += size
    fitted = ReviewDiff(kept, base=diff.base, target=diff.target, base_label=diff.base_label)
    return fitted, left


def fit_edits(edits: Sequence[tuple[str, str]], budget: int) -> tuple[list[tuple[str, str]], list[str]]:
    """The edits, in the order made, that fit in ``budget`` characters; the rest, by path."""
    kept: list[tuple[str, str]] = []
    left: list[str] = []
    used = 0
    for path, patch in edits:
        if used + len(patch) > budget:
            left.append(path)
            continue
        kept.append((path, patch))
        used += len(patch)
    return kept, left
