"""A failed attempt has a class before it has a retry (arXiv 2606.01416).

The loop's two recovery policies are the two losing baselines of the fault-injection study: a
retry briefed with generic feedback (manager prose plus verifier output, joined in
``autonomous.py``) and an unconditional escalation to a stronger model. Recovery *targeted at the
failure class* won at every matched budget — 98.8% against 94.5% for retry-only — and the gap was
widest at one recovery attempt, which is the budget a three-attempt loop actually has.

Two pure functions, no model call anywhere:

* :func:`classify_failure` reads what an :class:`~chimera.core.autonomous.Attempt` already carries
  and returns one class with the **evidence** it fired on — the exact field or line, so a reviewer
  can see why and a test can assert the reason rather than the label.
* :func:`targeted_feedback` turns that class into the brief the next attempt gets. ``UNKNOWN`` is
  the generic string, unchanged.

Precedence is fixed and runs from "the attempt never finished" to "it finished and was judged":
``BUDGET`` · ``TIMEOUT`` · ``TOOL_SKIP`` · ``HOLLOW_SUCCESS`` · ``BUILD_ERROR`` · ``FAILING_TEST``
· ``REVERTED`` · ``UNKNOWN``. The two unchanged-tree classes sit above the verifier classes on
purpose: a failing test on a tree the attempt never touched is the *starting* state, not evidence
about what the attempt did wrong. ``REVERTED`` sits below them because every failed attempt with a
guard is rolled back — it is the class for a rollback whose cause the verifier output does not
name (a manager, contract or checklist rejection, a custom check script), where the diff plus the
gate's reason is the most targeted brief available.

**Deliberately absent.** 2607.04686's taxonomy also names *result ignored* (a tool errored and the
agent carried on as if it had not) and *fabrication* (the answer claims work the tool log does not
show). Neither is shipped. The attempt carries ``tool_names`` but not the transcript, so "ignored"
cannot be read from what reaches this module; and "claims work" is a judgment about prose, which
no regex makes deterministically — a heuristic here would look like a detector and be a guess.
``UNKNOWN`` is what those cases become, and it is not a defect: it is the honest answer.

Every detector reads a field the loop already computes. ``stopped_reason`` is the one exception —
it is the worker's, not the attempt's — and the loop passes it in from the result it holds at the
moment it classifies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from chimera.governance.ledger import EXEC_TOOLS, WRITE_TOOLS

if TYPE_CHECKING:
    from chimera.core.autonomous import Attempt

#: The two briefs a retry can get. One source, imported by the loop and the CLI, so a misspelled
#: mode is refused at both rather than quietly behaving as ``generic`` at one of them.
RECOVERY_MODES = frozenset({"generic", "targeted"})

#: Evidence and feedback bodies are bounded: they go on the receipt and into a prompt.
_EVIDENCE_CHARS = 500


class FailureClass(StrEnum):
    """What kind of failure an attempt was. The value is what the receipt carries."""

    BUDGET = "budget"  # the dollar ceiling stopped the worker mid-attempt
    TIMEOUT = "timeout"  # the step ceiling stopped the worker mid-attempt
    TOOL_SKIP = "tool_skip"  # the tree is unchanged and no write/exec tool was even called
    HOLLOW_SUCCESS = "hollow_success"  # the tree is unchanged although a tool was called
    BUILD_ERROR = "build_error"  # the verifier's output says the code could not be loaded
    FAILING_TEST = "failing_test"  # the verifier's output names a failing test
    REVERTED = "reverted"  # rolled back, and the verifier output names no cause
    UNKNOWN = "unknown"  # every detector declined — never a guess


@dataclass(frozen=True)
class ClassifiedFailure:
    """A class and the exact thing it was read from."""

    cls: FailureClass
    evidence: str = ""


# --- detectors -------------------------------------------------------------------------------------

#: The worker's own vocabulary (``AgentResult.stopped_reason``), matched exactly. ``spend`` and
#: ``budget`` are the two spellings the loop itself checks before finalising a capped run.
_CEILINGS = {
    "spend": FailureClass.BUDGET,
    "budget": FailureClass.BUDGET,
    "max_steps": FailureClass.TIMEOUT,
}

#: A line saying the code under test could not be LOADED — the ``Name: message`` line a Python
#: traceback ends with for its import-time exceptions (with or without pytest's ``E`` gutter),
#: tsc's numbered errors, rustc's. Anchored at the line start so a mention inside a message does
#: not count, and the colon is required so pytest's own ``ImportError while importing test
#: module`` header does not outrank the exception line that says what went wrong. The
#: interpreter's unquoted ``No module named pytest`` never reaches here: the loop already treats
#: that as an abstaining verifier.
_BUILD_LINE = re.compile(
    r"^(?:E\s+)?(?:SyntaxError|IndentationError|TabError|ModuleNotFoundError|ImportError):.*$"
    r"|^.*\berror TS\d+:.*$"
    r"|^error\[E\d+\]:.*$",
    re.MULTILINE,
)

#: A line naming a failing test: pytest's short summary, unittest's ``FAIL: name (id)`` — the
#: parenthesised id is required, so an ``ERROR: usage:`` line from a mis-invoked command is not
#: a test — and Go's ``--- FAIL:``. The test id is the payload; the summary line carries it and
#: the message.
_TEST_ID_LINE = re.compile(
    r"^FAILED \S+.*$|^(?:FAIL|ERROR): \w+ \(.*$|^--- FAIL: \S+.*$",
    re.MULTILINE,
)

#: The assertion under pytest's ``E`` gutter, or a bare ``AssertionError`` line.
_ASSERTION_LINE = re.compile(r"^E\s+.*$|^AssertionError\b.*$", re.MULTILINE)

#: ``path:line`` in the three shapes the runners print: pytest/gcc ``path:12:``, a Python
#: traceback ``File "path", line 12``, tsc ``path(12,5)``.
_LOCATION = re.compile(
    r"^([\w./\\-]+\.\w+):(\d+)(?::\d+)?:"
    r"|File \"([^\"]+)\", line (\d+)"
    r"|^([\w./\\-]+\.\w+)\((\d+),\d+\):",
    re.MULTILINE,
)

#: A file the task names — a token with a source-file extension. Deterministic and narrow: it
#: exists to name the file(s) a forced-action brief should point at, never to invent one.
_FILE_IN_TASK = re.compile(
    r"(?<![\w/\\.-])[\w./\\-]*\w\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|ya?ml|toml|md|txt|html|"
    r"css|scss|rs|go|java|kt|rb|sh|ps1|cfg|ini|sql|c|h|cpp|hpp|cs|swift)\b"
)


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    return match.group(0).strip() if match else ""


def _location(text: str) -> str:
    match = _LOCATION.search(text or "")
    if match is None:
        return ""
    groups = [g for g in match.groups() if g]
    return f"{groups[0]}:{groups[1]}" if len(groups) >= 2 else ""


def _acted(tool_names: list[str]) -> bool:
    """Did the attempt call anything that can change the tree or run something in it?"""
    return any(name in WRITE_TOOLS or name in EXEC_TOOLS for name in tool_names)


def classify_failure(attempt: Attempt, *, stopped_reason: str = "") -> ClassifiedFailure:
    """One class for a failed attempt, with the evidence it was read from.

    ``stopped_reason`` is the worker's (``AgentResult.stopped_reason``); everything else is read
    off the attempt. Pure and deterministic: the same attempt always gets the same answer.
    """
    if attempt.success:
        raise ValueError("a successful attempt has no failure class")

    if stopped_reason in _CEILINGS:
        return ClassifiedFailure(_CEILINGS[stopped_reason], f"stopped_reason={stopped_reason}")

    # Unchanged tree, MEASURED — `None` is "nobody looked" and never fires either class. On a
    # failed attempt an unchanged tree always means a change was required: with no active
    # verifier the loop fails an unchanged attempt by default, and with one the tests failed on
    # a tree the attempt never touched.
    if attempt.diff_productive is False:
        if not _acted(attempt.tool_names):
            evidence = (
                f"tool_names={attempt.tool_names!r} (no write/exec call); diff_productive=False"
            )
            return ClassifiedFailure(FailureClass.TOOL_SKIP, evidence[:_EVIDENCE_CHARS])
        evidence = "diff_productive=False" + (
            f"; {attempt.diff_summary}" if attempt.diff_summary else ""
        )
        return ClassifiedFailure(FailureClass.HOLLOW_SUCCESS, evidence[:_EVIDENCE_CHARS])

    # Only a verifier that actually judged the attempt has output worth reading: `evidence` is
    # "verifier" exactly when one was active, and an abstaining verifier's text is not a verdict.
    if attempt.evidence == "verifier":
        if line := _first(_BUILD_LINE, attempt.verify_output):
            return ClassifiedFailure(FailureClass.BUILD_ERROR, line[:_EVIDENCE_CHARS])
        if line := _first(_TEST_ID_LINE, attempt.verify_output):
            return ClassifiedFailure(FailureClass.FAILING_TEST, line[:_EVIDENCE_CHARS])

    if attempt.reverted and attempt.diffs:
        paths = ", ".join(d.path for d in attempt.diffs)
        evidence = f"reverted=True; {len(attempt.diffs)} file(s): {paths}"
        return ClassifiedFailure(FailureClass.REVERTED, evidence[:_EVIDENCE_CHARS])

    return ClassifiedFailure(FailureClass.UNKNOWN, "")


# --- the brief -------------------------------------------------------------------------------------


def _files_named_in(task: str) -> list[str]:
    seen: list[str] = []
    for token in _FILE_IN_TASK.findall(task or ""):
        if token not in seen:
            seen.append(token)
    return seen[:10]


def targeted_feedback(
    classified: ClassifiedFailure, attempt: Attempt, *, generic: str, task: str = ""
) -> str:
    """The brief the next attempt gets for this class; ``generic`` unchanged when there is none.

    ``generic`` is the string the loop composed today — passed in rather than rebuilt here, so
    there is exactly one place that knows how it is composed and ``UNKNOWN`` cannot drift from it.
    ``task`` is the raw request, read only to name the file(s) a forced-action brief points at.
    """
    cls = classified.cls
    if cls is FailureClass.FAILING_TEST:
        parts = ["Verification failed on a specific test. Fix exactly this:", classified.evidence]
        if assertion := _first(_ASSERTION_LINE, attempt.verify_output):
            parts.append(assertion)
        if where := _location(attempt.verify_output):
            parts.append(f"at {where}")
        return "\n".join(parts)
    if cls is FailureClass.BUILD_ERROR:
        parts = [
            "The code does not build or import, so nothing else about it can be judged. Fix "
            "exactly this first:",
            classified.evidence,
        ]
        if where := _location(attempt.verify_output):
            parts.append(f"at {where}")
        return "\n".join(parts)
    if cls is FailureClass.REVERTED:
        from chimera.core.autonomous import _rendered_diff  # the one renderer; avoids a cycle

        cause = attempt.verify_output if attempt.evidence == "verifier" else attempt.feedback
        body = _rendered_diff(attempt.diffs)
        if not body:
            return generic
        return (
            "This is what was tried and undone — do not repeat it, fix the cause:\n"
            f"{cause.strip() or 'the attempt was rejected'}\n\n"
            f"Reverted diff:\n{body}"
        )
    if cls is FailureClass.TOOL_SKIP:
        acting = ", ".join(sorted(WRITE_TOOLS | EXEC_TOOLS))
        return (
            "You produced prose; the task requires running or editing. The workspace did not "
            f"change and no write or exec tool was called ({classified.evidence}). Your next "
            f"step must be a tool call: one of {acting}."
        )
    if cls is FailureClass.HOLLOW_SUCCESS:
        named = _files_named_in(task)
        target = (
            f"Files the task names: {', '.join(named)}. Your next tool call must write to one "
            "of them."
            if named
            else "Locate the responsible file; your next tool call must write to it."
        )
        return (
            f"No productive change reached the workspace ({classified.evidence}). An "
            f"explanation is not a fix: edit the code. {target}"
        )
    if cls is FailureClass.TIMEOUT:
        return (
            "The attempt hit the step ceiling before it finished "
            f"({classified.evidence}) and the workspace was reverted. Spend the steps on the "
            "change: read only what you must, make the edit, run the verifier once. Do not "
            "repeat the exploration."
        )
    # BUDGET never gets a retry, and UNKNOWN has nothing more specific to say.
    return generic
