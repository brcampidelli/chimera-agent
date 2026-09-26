"""The human form of a review: findings first, then what the review could not see.

Plain text, not markup: every string in a finding was written by a model reading somebody's code,
and a bracket in it must print as a bracket. The command prints this with markup off.
"""

from __future__ import annotations

from chimera.review.report import Finding, ReviewReport

_INDENT = "    "


def _finding(f: Finding) -> list[str]:
    lines = [f"{f.priority}  {f.file}:{f.line}  {f.title or '(untitled)'}"]
    if f.evidence:
        lines.append(f"{_INDENT}evidence: {f.evidence}")
    if f.consequence:
        lines.append(f"{_INDENT}consequence: {f.consequence}")
    tail = []
    if f.confidence is not None:
        tail.append(f"confidence {f.confidence:.2f}")
    if f.verdict is not None and f.verdict.stage != "none":
        verdict = f"{f.verdict.stage}: {f.verdict.label}"
        tail.append(verdict + (f" ({f.verdict.reason})" if f.verdict.reason else ""))
    if tail:
        lines.append(_INDENT + " · ".join(tail))
    return lines


def _section(title: str, items: list[str], empty: str) -> list[str]:
    return ["", title, *([f"  - {i}" for i in items] or [f"  - {empty}"])]


def _headline(report: ReviewReport) -> str:
    against = report.base_label or report.target
    scope = f"{report.files_changed} file(s) changed ({against})"
    if report.status == "empty":
        return f"Review (experimental): nothing to review, no changes ({against})."
    if report.status == "findings":
        return f"Review (experimental): {len(report.findings)} finding(s) in {scope}."
    if report.status == "no_findings":
        return f"Review (experimental): no findings in {scope}."
    return f"Review (experimental): incomplete, {len(report.findings)} finding(s) in {scope}."


def render_text(report: ReviewReport, *, show_dropped: bool = False) -> str:
    out = [_headline(report)]
    if report.status == "empty":
        return "\n".join(out) + "\n"
    for finding in report.findings:
        out += ["", *_finding(finding)]
    if report.status == "no_findings":
        out += [
            "",
            "No findings: nothing the reviewer reported survived the checks. That is not proof the "
            "change is correct; the risks and untested paths below are what it could not settle.",
        ]
    if report.status == "incomplete":
        out += [
            "",
            "Incomplete: part of the change was not reviewed (listed below), so the absence of a "
            "finding there says nothing.",
        ]
    out += _section("Residual risks", report.residual_risks, "none named by the reviewer")
    out += _section("Untested paths", report.untested_paths, "none named")
    if report.not_reviewed:
        out += _section("Not reviewed", [f"{n.file}: {n.reason}" for n in report.not_reviewed], "")
    if report.untracked_skipped:
        out += ["", f"{report.untracked_skipped} untracked file(s) were not reviewed."]
    if report.dropped:
        if show_dropped:
            out += ["", "Dropped by the checks"]
            for finding in report.dropped:
                out += ["", *_finding(finding)]
        else:
            out += ["", f"{len(report.dropped)} finding(s) dropped by the checks (--show-dropped)."]
    for note in report.notes:
        out += ["", f"Note: {note}"]
    who = report.reviewer
    cost = "unknown" if report.usage.usd is None else f"US$ {report.usage.usd:.4f}"
    out += [
        "",
        f"Reviewer {who.model} ({who.family}), author {who.author_model} ({who.author_family}), "
        f"verifier {who.verifier}.",
        f"{report.usage.calls} call(s), {report.usage.prompt_tokens:,} tokens in, "
        f"{report.usage.completion_tokens:,} out, {cost}.",
    ]
    return "\n".join(out) + "\n"
