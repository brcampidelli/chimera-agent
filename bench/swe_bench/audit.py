"""Audit of the published SWE-bench lift (study 30, S30-35; PREREGISTRATION.md Amendment 6).

Reads only what the four runs already left on disk — predictions, the harness's per-arm reports, and
the `eval.sh` the harness wrote for every graded instance — and re-reads the published paired
comparisons three ways:

0. **as graded** — the control. Every published delta and CI must come back out of the raw reports,
   or the audit aborts: a control that does not reproduce is two wrong numbers, not a comparison.
1. **strict** — every resolved patch that edits a test file counts as unresolved.
2. **harness-aware** — only a resolved patch whose test edit lies OUTSIDE the files the harness
   resets before grading counts as unresolved. The harness checks the official test patch's files
   out from the base commit before applying it (visible in each `eval.sh`), so an edit there was
   overwritten and cannot have helped; an edit elsewhere (a fixture, a helper module) could have.

The stronger-test gradings (full developer suites, SWE-ABS) need Docker and are not run here.

    python bench/swe_bench/audit.py            # prints the readings, writes results/audit_s30_35.json
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chimera.eval.paired import PairedResult, compare_paired  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
OUT = RESULTS / "audit_s30_35.json"

# `\r?` because a patch written on Windows ends its headers in CRLF; without it the header is not
# matched at all and the test edit vanishes on the NON-conservative side (read as "touches no test").
_DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)\r?$", re.MULTILINE)
_ANY_HEADER = re.compile(r"^diff --git ", re.MULTILINE)
# A plain unified diff (no `diff --git` line) names its files only in the `---`/`+++` pair, and the
# harness's `git apply` accepts it. Read the pair too: a test edit named only there would otherwise
# vanish on the NON-conservative side. An optional tab-separated timestamp follows the path in
# diffs made by `diff -u`.
_FILE_PAIR = re.compile(
    r"^--- (\S+)(?:\t[^\r\n]*)?\r?\n\+\+\+ (\S+)(?:\t[^\r\n]*)?\r?$", re.MULTILINE
)
_ANY_PLUS_HEADER = re.compile(r"^\+\+\+ ", re.MULTILINE)
_TEST_NAME = re.compile(r"^(test_.*|.*_tests?|tests)\.py$")


@dataclass(frozen=True)
class Arm:
    """One graded arm: where its predictions, its harness report and its eval logs live."""

    key: str
    run: str
    predictions: str
    report: str
    logs: str


ARMS: dict[str, Arm] = {
    a.key: a
    for a in (
        Arm(
            "run1/baseline",
            "run1",
            "predictions_baseline.jsonl",
            "chimera-baseline.run1_baseline.json",
            "logs/run_evaluation/run1_baseline",
        ),
        Arm(
            "run1/treatment",
            "run1",
            "predictions_treatment.jsonl",
            "chimera-treatment.run1_treatment.json",
            "logs/run_evaluation/run1_treatment",
        ),
        Arm(
            "run2/baseline",
            "run2",
            "predictions_baseline.jsonl",
            "chimera-baseline.run2_baseline.json",
            "logs/run_evaluation/run2_baseline",
        ),
        Arm(
            "run2/treatment_diff",
            "run2",
            "predictions_treatment_diff.jsonl",
            "chimera-treatment_diff.run2_treatment_diff.json",
            "logs/run_evaluation/run2_treatment_diff",
        ),
        Arm(
            "run3/baseline",
            "run3",
            "predictions_baseline.jsonl",
            "chimera-baseline.run3_baseline.json",
            "logs/run_evaluation/run3_baseline",
        ),
        Arm(
            "run3/treatment_diff",
            "run3",
            "predictions_treatment_diff.jsonl",
            "chimera-treatment_diff.run3_treatment_diff.json",
            "logs/run_evaluation/run3_treatment_diff",
        ),
        Arm(
            "run4/treatment",
            "run4",
            "predictions_treatment.jsonl",
            "chimera-treatment.run4_treatment.json",
            "logs/run_evaluation/run4_treatment",
        ),
    )
}

# The comparisons RESULTS.md publishes, with the delta and CI it prints (rounded to 0.1 pp). The
# pooled row is run 2 + run 3 concatenated; run 4 reuses run 3's baseline and scaffold+gate arms.
PUBLISHED: dict[str, tuple[tuple[str, ...], tuple[str, ...], float, tuple[float, float]]] = {
    "run 1": (("run1/baseline",), ("run1/treatment",), 0.0, (-0.085, 0.085)),
    "run 2": (("run2/baseline",), ("run2/treatment_diff",), 0.158, (-0.019, 0.158)),
    "run 3": (("run3/baseline",), ("run3/treatment_diff",), 0.098, (-0.035, 0.167)),
    "pooled": (
        ("run2/baseline", "run3/baseline"),
        ("run2/treatment_diff", "run3/treatment_diff"),
        0.117,
        (0.008, 0.164),
    ),
    "run 4 scaffold vs baseline": (("run3/baseline",), ("run4/treatment",), 0.049, (-0.076, 0.142)),
    "run 4 gate vs scaffold": (
        ("run4/treatment",),
        ("run3/treatment_diff",),
        0.049,
        (-0.076, 0.142),
    ),
}


def edited_files(patch: str) -> list[str]:
    """Every path a unified git diff touches, old and new side, in order, without duplicates.

    Both sides count: a rename OUT of a test file and a rename INTO one both edit tests.

    Both header forms are read: the `diff --git a/X b/Y` line and the `--- a/X` / `+++ b/Y` pair,
    whose first path component is the `-p1` prefix `git apply` strips; `/dev/null` is no file.

    Raises ValueError when a `diff --git` header or a `+++` line is not parsed — a path git quoted
    (non-ASCII), a path with a space, a `+++` with no `---` above it. A header that is silently skipped
    is a file read as "not a test", which turns a possible test edit into a pass; refusing is the only
    safe answer the parser can give.
    """
    headers = _DIFF_HEADER.findall(patch)
    total = len(_ANY_HEADER.findall(patch))
    if total != len(headers):
        raise ValueError(f"{total - len(headers)} of {total} diff headers could not be parsed")
    pairs = _FILE_PAIR.findall(patch)
    plus = len(_ANY_PLUS_HEADER.findall(patch))
    if plus != len(pairs):
        raise ValueError(f"{plus - len(pairs)} of {plus} '+++' file headers could not be parsed")
    seen: dict[str, None] = {}
    for old, new in headers:
        seen.setdefault(old)
        seen.setdefault(new)
    for pair in pairs:
        for path in pair:
            if path != "/dev/null":
                seen.setdefault(path.split("/", 1)[1] if "/" in path else path)
    return list(seen)


def is_test_path(path: str) -> bool:
    """A file the repository treats as a test.

    A `tests` directory anywhere in the path, or a test-named module. django's own `django/test/`
    package is the test FRAMEWORK — editing it is editing the product — so a bare `test` directory
    does not count, and `testcases.py` is not a test-named file.
    """
    parts = path.split("/")
    return "tests" in parts[:-1] or bool(_TEST_NAME.match(parts[-1]))


def reset_files(eval_sh: str) -> set[str] | None:
    """The files the harness checked out from the base commit before applying the official test patch.

    That is the first `git checkout <sha> <files...>` in the script. None when the script has no such
    line — which is not the same as "resets nothing", so the caller can refuse to read it as safe.
    """
    for line in eval_sh.splitlines():
        words = shlex.split(line) if line.startswith("git checkout ") else []
        if len(words) >= 4 and re.fullmatch(r"[0-9a-f]{7,40}", words[2]):
            return set(words[3:])
    return None


@dataclass
class InstanceAudit:
    instance_id: str
    resolved: bool
    test_files: list[str] = field(default_factory=list)
    reset: set[str] | None = None
    has_eval_sh: bool = True

    @property
    def touches_tests(self) -> bool:
        return bool(self.test_files)

    @property
    def live_test_edits(self) -> list[str]:
        """Test edits the harness did NOT overwrite. Unknown reset set: all of them (conservative)."""
        if self.reset is None:
            return list(self.test_files)
        return [p for p in self.test_files if p not in self.reset]


def audit_arm(arm: Arm, root: Path = RESULTS) -> dict[str, InstanceAudit]:
    run_dir = root / arm.run
    report = json.loads((run_dir / arm.report).read_text(encoding="utf-8"))
    resolved = set(report["resolved_ids"])
    out: dict[str, InstanceAudit] = {}
    for line in (run_dir / arm.predictions).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        pred = json.loads(line)
        iid = pred["instance_id"]
        try:
            files = edited_files(pred.get("model_patch") or "")
        except ValueError as exc:
            raise SystemExit(f"{arm.key} {iid}: {exc}; the patch cannot be audited") from exc
        tests = [p for p in files if is_test_path(p)]
        eval_sh = next((run_dir / arm.logs).glob(f"*/{iid}/eval.sh"), None)
        reset = reset_files(eval_sh.read_text(encoding="utf-8")) if eval_sh else None
        out[iid] = InstanceAudit(iid, iid in resolved, tests, reset, eval_sh is not None)
    unknown = resolved - set(out)
    if unknown:
        raise SystemExit(
            f"{arm.key}: report resolves instances with no prediction: {sorted(unknown)}"
        )
    return out


def outcomes(audits: Mapping[str, InstanceAudit], reading: str) -> dict[str, bool]:
    """Pass/fail per instance under one reading. Only ever turns a resolved instance into a failure."""
    if reading == "as_graded":
        return {i: a.resolved for i, a in audits.items()}
    if reading == "strict":
        return {i: a.resolved and not a.touches_tests for i, a in audits.items()}
    if reading == "harness_aware":
        return {i: a.resolved and not a.live_test_edits for i, a in audits.items()}
    raise ValueError(f"unknown reading {reading!r}")


def compare(
    audited: Mapping[str, Mapping[str, InstanceAudit]],
    baseline: Iterable[str],
    treatment: Iterable[str],
    reading: str,
) -> PairedResult:
    """Pair the arms instance by instance — concatenating runs for the pooled comparison."""
    base: list[bool] = []
    treat: list[bool] = []
    for b_key, t_key in zip(baseline, treatment, strict=True):
        b, t = outcomes(audited[b_key], reading), outcomes(audited[t_key], reading)
        if set(b) != set(t):
            raise SystemExit(
                f"{b_key} and {t_key} are not over the same instances; they cannot be paired"
            )
        for iid in sorted(b):
            base.append(b[iid])
            treat.append(t[iid])
    return compare_paired(base, treat)


def check_control(audited: Mapping[str, Mapping[str, InstanceAudit]]) -> list[str]:
    """Every way the as-graded reading fails to reproduce the published table. Empty = control holds."""
    failures: list[str] = []
    for label, (base, treat, delta, ci) in PUBLISHED.items():
        got = compare(audited, base, treat, "as_graded")
        lo, hi = got.diff_ci
        if round(got.delta, 3) != delta or (round(lo, 3), round(hi, 3)) != ci:
            failures.append(
                f"{label}: published {delta:+.1%} {ci}, reproduced {got.delta:+.1%} ({lo:.3f}, {hi:.3f})"
            )
    return failures


def summarize_arm(audits: Mapping[str, InstanceAudit]) -> dict[str, object]:
    """One arm's counts for the report.

    `resolved_without_eval_sh` is listed for EVERY resolved instance, not only the test-touching ones:
    Amendment 6 promises that a missing `eval.sh` is reported, and a `reset_by_harness: null` buried in
    the per-instance block cannot tell "no log" from "a log with no checkout line".
    """
    touching = {i: a for i, a in audits.items() if a.resolved and a.touches_tests}
    return {
        "resolved": sum(a.resolved for a in audits.values()),
        "resolved_touching_tests": len(touching),
        "resolved_with_live_test_edits": sum(bool(a.live_test_edits) for a in touching.values()),
        "resolved_without_eval_sh": sorted(
            i for i, a in audits.items() if a.resolved and not a.has_eval_sh
        ),
        "instances": {
            i: {
                "test_files": a.test_files,
                "reset_by_harness": sorted(a.reset) if a.reset is not None else None,
                "live_test_edits": a.live_test_edits,
            }
            for i, a in sorted(touching.items())
        },
    }


def main() -> int:
    audited = {key: audit_arm(arm) for key, arm in ARMS.items()}
    failures = check_control(audited)
    if failures:
        print(
            "CONTROL FAILED - the raw reports do not reproduce the published table; no reading is made:"
        )
        for f in failures:
            print("  " + f)
        return 1

    arms_out = {key: summarize_arm(audits) for key, audits in audited.items()}
    paired = {
        reading: {
            label: compare(audited, b, t, reading) for label, (b, t, _, _) in PUBLISHED.items()
        }
        for reading in ("as_graded", "strict", "harness_aware")
    }
    readings = {
        reading: {label: r.summary() for label, r in rows.items()}
        for reading, rows in paired.items()
    }
    report = {
        "registration": "bench/swe_bench/PREREGISTRATION.md, Amendment 6",
        "arms": arms_out,
        "readings": readings,
        "owed": ["official re-grade", "full developer suites", "SWE-ABS strengthened tests"],
    }
    OUT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )

    print("control: every published delta and CI reproduced from the raw reports\n")
    for key, a in arms_out.items():
        print(
            f"{key:22} resolved {a['resolved']:2}  touching tests {a['resolved_touching_tests']}"
            f"  live test edits {a['resolved_with_live_test_edits']}"
            f"  resolved without eval.sh {sum(x.resolved and not x.has_eval_sh for x in audited[key].values())}"
        )
    for reading, results in paired.items():
        print(f"\n[{reading}]")
        for label, r in results.items():
            lo, hi = r.diff_ci
            print(
                f"  {label:28} {r.baseline_rate:.1%} -> {r.treatment_rate:.1%}"
                f"  delta {r.delta:+.1%}  CI [{lo:+.1%}, {hi:+.1%}]  significant={r.significant}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
