"""SWE-bench Verified-Mini adapter — the second standard scoreboard for the weak-model-lift thesis.

Terminal-Bench proves the thesis on CLI tasks; SWE-bench proves it on real GitHub bug-fixes: given
a repo at a base commit and an issue, the agent must produce a patch that makes the instance's
FAIL_TO_PASS tests pass while keeping PASS_TO_PASS green. "Verified" is the human-validated subset;
"Mini" is a small curated slice of it for a cheap, fast A/B. The comparison is the same honest one:
a FREE model alone vs the SAME model driven by Chimera on the SAME instance ids, delta with a CI
(see :mod:`chimera.eval.bench_ab`).

Like the Terminal-Bench adapter, this is honest about its boundary. The pure, unit-tested parts are
here: the ``chimera solve`` invocation per instance (the treatment arm) and the parsing of the
official SWE-bench evaluation report into the pass/fail trials the A/B consumes. What is NOT here —
deliberately — is the dataset and the Docker evaluation harness: those are heavy and opt-in, and the
pass/fail verdict must come from SWE-bench's own test-based grading, never something we self-report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.bench_ab import ABResult, compare
from chimera.eval.terminal_bench import command_string

# SWE-bench instances are multi-file bug-fixes with several constraints, so the scaffolding under
# test adds the requirement checklist on top of the code-navigation flags — the pieces the thesis
# says lift a weak model on exactly this kind of task. Baseline (Arm A) uses none of this.
_DEFAULT_FLAGS: tuple[str, ...] = ("--repo-map", "--progress-ledger", "--replan", "--checklist")

_INSTRUCTION = (
    "You are working in the checked-out repository. Resolve this issue by editing the code so the "
    "project's tests pass. Do not edit the tests.\n\nIssue:\n{problem}"
)


@dataclass(frozen=True)
class SWEInstance:
    """One SWE-bench task: a repo at a commit, an issue, and the tests that grade a fix."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    test_cmd: str = ""  # the command that runs FAIL_TO_PASS + PASS_TO_PASS (exit 0 == resolved)
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SWEInstance:
        return cls(
            instance_id=str(data["instance_id"]),
            repo=str(data.get("repo", "")),
            base_commit=str(data.get("base_commit", "")),
            problem_statement=str(data.get("problem_statement", "")),
            test_cmd=str(data.get("test_cmd", "")),
            fail_to_pass=_as_str_list(data.get("FAIL_TO_PASS", data.get("fail_to_pass", []))),
            pass_to_pass=_as_str_list(data.get("PASS_TO_PASS", data.get("pass_to_pass", []))),
        )


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):  # the dataset sometimes stores these as a JSON-encoded string
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value]
    return [str(v) for v in value] if isinstance(value, list) else []


def load_instances(path: Path) -> list[SWEInstance]:
    """Load a JSONL slice of SWE-bench (Verified-Mini) — one instance object per line."""
    instances: list[SWEInstance] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            instances.append(SWEInstance.from_dict(json.loads(line)))
    return instances


def build_solve_command(
    instance: SWEInstance,
    *,
    model: str,
    workspace: str = ".",
    flags: tuple[str, ...] = _DEFAULT_FLAGS,
    max_attempts: int = 3,
) -> list[str]:
    """The argv for the ``chimera solve`` run that attempts one SWE-bench instance.

    Deterministic and side-effect-free. The issue becomes the task; when the instance carries a
    ``test_cmd`` it is passed as ``--verify`` so the agent gets executable ground truth (verify or
    revert) — the same test-based signal the harness grades on. ``flags`` are the scaffolding under
    test.
    """
    task = _INSTRUCTION.format(problem=instance.problem_statement)
    argv = ["chimera", "solve", task, "--model", model, "--workspace", workspace,
            "--max-attempts", str(max_attempts)]
    if instance.test_cmd:
        argv.extend(["--verify", instance.test_cmd])
    argv.extend(flags)
    return argv


def parse_report(report: dict[str, Any]) -> dict[str, bool]:
    """Normalize a SWE-bench evaluation report into ``{instance_id: resolved}``.

    Accepts the two shapes the harness/leaderboard emit: a summary with a ``resolved_ids`` list
    (everything else is unresolved), or a per-instance map whose values are a bool or a dict with a
    ``resolved`` flag.
    """
    if "resolved_ids" in report:
        resolved = {str(i) for i in report.get("resolved_ids", [])}
        unresolved = {str(i) for i in report.get("unresolved_ids", [])}
        return {i: True for i in resolved} | {i: False for i in unresolved}
    out: dict[str, bool] = {}
    for instance_id, value in report.items():
        if isinstance(value, bool):
            out[str(instance_id)] = value
        elif isinstance(value, dict) and "resolved" in value:
            out[str(instance_id)] = bool(value["resolved"])
    return out


def report_to_trials(report: dict[str, Any], instance_ids: list[str]) -> list[bool]:
    """Project a report onto an ordered instance-id list (missing == unresolved == False).

    Fixing the id list keeps both arms of the A/B on the *same* instances — the honesty guard that
    makes the delta meaningful.
    """
    resolved = parse_report(report)
    return [resolved.get(instance_id, False) for instance_id in instance_ids]


def compare_arms(
    baseline_report: dict[str, Any],
    treatment_report: dict[str, Any],
    instance_ids: list[str],
) -> ABResult:
    """A/B two SWE-bench evaluation reports over the same instance ids (reuses the honest engine)."""
    return compare(
        report_to_trials(baseline_report, instance_ids),
        report_to_trials(treatment_report, instance_ids),
        baseline_name="model-only",
        treatment_name="model+chimera",
    )


__all__ = [
    "SWEInstance",
    "load_instances",
    "build_solve_command",
    "command_string",
    "parse_report",
    "report_to_trials",
    "compare_arms",
]
