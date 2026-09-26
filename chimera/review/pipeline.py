"""A review end to end: batch the diff, find, anchor, verify, order, report.

The stages and what each may do to a finding:

1. **Finder** (a model): proposes findings, all of them, with a confidence.
2. **Anchor** (arithmetic): a finding whose ``file:line`` is not in the diff is dropped, with that
   reason written on it. This is the cautious verifier's first ground, decided without a model.
3. **Verifier** (a model, one finding per call): may drop a finding only on the grounds its prompt
   names; an unreadable or failed check keeps the finding.

Nothing leaves silently. A dropped finding is in ``ReviewReport.dropped`` with its stage and reason,
and a part of the change that was never reviewed is in ``not_reviewed`` and makes the status
``incomplete`` rather than ``no_findings``.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from chimera.providers.gateway import SupportsComplete
from chimera.review.diff import FileDiff, ReviewDiff
from chimera.review.family import ReviewerChoice
from chimera.review.finder import find
from chimera.review.report import (
    Finding,
    NotReviewed,
    Reviewer,
    ReviewReport,
    Usage,
    Verdict,
    number,
    order,
)
from chimera.review.verifier import Verifier

#: Rendered diff per finder call. One call per batch keeps a small change to one call, and splits a
#: large one so no single reply has to cover more than a model reads well.
MAX_BATCH_CHARS = 48_000
#: A file whose own diff is larger than this is listed as not reviewed rather than truncated: a
#: review of the first half of a file reads as a review of the file.
MAX_FILE_CHARS = 120_000
VERIFY_WORKERS = 4

_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.|\.(test|spec)\.")
_CODE_SUFFIX = re.compile(
    r"\.(py|pyi|ts|tsx|js|jsx|mjs|go|rs|java|kt|rb|php|c|cc|cpp|h|hpp|cs|swift|scala|sh)$"
)


def _batches(diff: ReviewDiff) -> tuple[list[list[FileDiff]], list[NotReviewed]]:
    batches: list[list[FileDiff]] = []
    skipped: list[NotReviewed] = []
    current: list[FileDiff] = []
    size = 0
    for f in sorted(diff.files, key=lambda f: f.path):
        if f.status == "binary":
            skipped.append(NotReviewed(file=f.path, reason="binary file"))
            continue
        if not f.hunks and f.status != "renamed":
            continue
        length = len("\n".join(f.render()))
        if length > MAX_FILE_CHARS:
            skipped.append(NotReviewed(file=f.path, reason=f"diff too large ({length:,} chars)"))
            continue
        if current and size + length > MAX_BATCH_CHARS:
            batches.append(current)
            current, size = [], 0
        current.append(f)
        size += length
    if current:
        batches.append(current)
    return batches, skipped


def _untested(diff: ReviewDiff) -> list[str]:
    """The changed code files, when the change touches no test at all. A heuristic, and labelled."""
    paths = [f.path for f in diff.files if f.status != "deleted"]
    if any(_TEST_PATH.search(p) for p in paths):
        return []
    code = [p for p in paths if _CODE_SUFFIX.search(p)]
    if not code:
        return []
    shown = ", ".join(code[:10]) + (f" and {len(code) - 10} more" if len(code) > 10 else "")
    return [f"no test file changed in this diff; changed code: {shown}"]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def review(
    diff: ReviewDiff,
    backend: SupportsComplete,
    reviewer: ReviewerChoice,
    verifier: Verifier,
    *,
    untracked_skipped: int = 0,
) -> ReviewReport:
    """Run every stage over ``diff`` and return the report."""
    who = Reviewer(
        model=reviewer.model, family=reviewer.family, author_model=reviewer.author_model,
        author_family=reviewer.author_family, source=reviewer.source,
        same_family=reviewer.same_family, verifier=verifier.name,
    )
    report = ReviewReport(
        status="empty", base=diff.base, base_label=diff.base_label, target=diff.target,
        files_changed=len(diff.files), untracked_skipped=untracked_skipped, reviewer=who,
    )
    report.notes += _reviewer_notes(reviewer)
    batches, report.not_reviewed = _batches(diff)
    if not batches and not report.not_reviewed:
        return report

    usage = Usage()
    located: list[tuple[Finding, FileDiff]] = []
    for batch in batches:
        reply = find(backend, reviewer.model, "\n\n".join("\n".join(f.render()) for f in batch))
        usage.calls += 1
        usage.prompt_tokens += reply.prompt_tokens
        usage.completion_tokens += reply.completion_tokens
        if not reply.ok:
            usage.failed_calls += 1
            reason = f"the reviewer's reply could not be used: {reply.error}"
            report.not_reviewed += [NotReviewed(file=f.path, reason=reason) for f in batch]
            continue
        report.residual_risks += reply.residual_risks
        report.untested_paths += reply.untested_paths
        if reply.unlocated:
            report.notes.append(f"{reply.unlocated} finding(s) named no file and line; not shown")
        for finding in reply.findings:
            file = diff.file(finding.file)
            if file is None or not file.anchors(finding.line):
                why = "the file is not in this diff" if file is None else "the line is not in it"
                verdict = Verdict(
                    state="dropped", label="not in the diff", reason=why, stage="anchor"
                )
                report.dropped.append(finding.model_copy(update={"verdict": verdict}))
                continue
            located.append((finding.model_copy(update={"file": file.path}), file))

    with ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as pool:
        verdicts = list(pool.map(lambda pair: verifier.check(*pair), located))
    kept: list[Finding] = []
    for (finding, _), verdict in zip(located, verdicts, strict=True):
        checked = finding.model_copy(update={"verdict": verdict})
        (report.dropped if verdict.state == "dropped" else kept).append(checked)

    calls, failed, prompt, completion = verifier.usage()
    usage.calls += calls
    usage.failed_calls += failed
    usage.prompt_tokens += prompt
    usage.completion_tokens += completion
    usage.usd = _price(reviewer.model, usage.prompt_tokens, usage.completion_tokens)

    report.findings = number(order(kept), "F")
    report.dropped = number(order(report.dropped), "D")
    report.residual_risks = _unique(report.residual_risks)
    report.untested_paths = _unique(report.untested_paths + _untested(diff))
    report.usage = usage
    # A binary file is outside what a text reviewer can read, by nature; anything else unreviewed
    # (a failed call, an unreadable reply, a file too large) is an accident, and silence about it
    # is not a clean review.
    if any(r.reason != "binary file" for r in report.not_reviewed):
        report.status = "incomplete"
    else:
        report.status = "findings" if report.findings else "no_findings"
    return report


def _reviewer_notes(reviewer: ReviewerChoice) -> list[str]:
    if not reviewer.same_family:
        return []
    if reviewer.source == "fallback":
        return [
            f"no model from a family other than {reviewer.author_family} is reachable with the "
            "configured keys, so the author's own family reviewed this change and shares its "
            "blind spots; set CHIMERA_REVIEW_MODEL or pass --reviewer-model"
        ]
    return [
        f"the reviewer ({reviewer.model}, chosen by {reviewer.source}) is from the author's family "
        f"({reviewer.author_family}), and a reviewer from that family shares its blind spots"
    ]


def _price(model: str, prompt: int, completion: int) -> float | None:
    from chimera.fusion.receipts import resolve_price

    price = resolve_price(model)
    if price is None:
        return None
    return round(prompt / 1e6 * price.input_per_m + completion / 1e6 * price.output_per_m, 6)
