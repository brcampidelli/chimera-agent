"""The SWE-bench lift is re-read with test-editing resolutions counted against the arm that made them.

`bench/swe_bench/audit.py` (S30-35, PREREGISTRATION.md Amendment 6) exists because a resolved patch
that also edits tests can pass for the wrong reason. Two readings, fixed before computing: *strict*
drops every resolved patch that touches a test file; *harness-aware* drops only those whose test edit
survives the harness's own reset of the official test patch's files. Both only ever turn a pass into
a failure — and neither is read at all unless the raw reports first reproduce the published table.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_AUDIT = Path(__file__).resolve().parent.parent / "bench" / "swe_bench" / "audit.py"
_RESULTS = _AUDIT.parent / "results"

# The run artefacts are committed, so CI reads them; a copy of the tree made without
# `bench/*/results` (the WSL gate does this) must skip rather than fail, as for every other bench.
needs_results = pytest.mark.skipif(
    not (_RESULTS / "run3" / "chimera-baseline.run3_baseline.json").exists()
    or not any((_RESULTS / "run3" / "logs").glob("*/*/*/*/eval.sh")),
    reason="bench/swe_bench/results (reports and eval logs) is not in this tree",
)


def _audit():
    spec = importlib.util.spec_from_file_location("swe_bench_audit", _AUDIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["swe_bench_audit"] = module
    spec.loader.exec_module(module)
    return module


def _patch(*paths: str) -> str:
    return "".join(
        f"diff --git a/{p} b/{p}\nindex 1..2 100644\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-a\n+b\n"
        for p in paths
    )


def test_every_path_a_diff_touches_is_read_on_both_sides_of_a_rename():
    audit = _audit()
    rename = "diff --git a/tests/old_test.py b/django/new.py\nsimilarity index 90%\n"
    assert audit.edited_files(_patch("django/a.py", "tests/x/tests.py") + rename) == [
        "django/a.py",
        "tests/x/tests.py",
        "tests/old_test.py",
        "django/new.py",
    ]
    assert audit.edited_files("") == []


@pytest.mark.parametrize(
    ("path", "is_test"),
    [
        ("tests/migrations/test_writer.py", True),
        (
            "tests/admin_views/models.py",
            True,
        ),  # a fixture module inside the test tree is still a test edit
        ("django/contrib/auth/tests.py", True),
        ("pkg/foo_test.py", True),
        ("test_top.py", True),
        # django's test FRAMEWORK is product code: editing it is not editing a test.
        ("django/test/testcases.py", False),
        ("django/test/utils.py", False),
        ("django/db/models/query.py", False),
        ("docs/topics/testing/overview.txt", False),
    ],
)
def test_a_test_file_is_told_from_the_test_framework(path: str, is_test: bool):
    assert _audit().is_test_path(path) is is_test


def test_the_reset_set_is_the_first_checkout_of_the_base_commit():
    audit = _audit()
    script = (
        "git status\ngit checkout 89d41cba392b759732ba9f1db4ff29ed47da6a56 tests/a/test_x.py tests/b/tests.py\n"
        "git apply -v - <<'EOF'\nEOF\ngit checkout 89d41cba392b759732ba9f1db4ff29ed47da6a56 tests/a/test_x.py\n"
    )
    assert audit.reset_files(script) == {"tests/a/test_x.py", "tests/b/tests.py"}
    # No checkout line is "unknown", not "resets nothing" — the caller must not read it as safe.
    assert audit.reset_files("git status\ngit apply -v -\n") is None


def test_a_test_edit_the_harness_overwrote_cannot_count_against_the_patch():
    audit = _audit()
    overwritten = audit.InstanceAudit("i", True, ["tests/a/test_x.py"], {"tests/a/test_x.py"})
    live = audit.InstanceAudit("j", True, ["tests/a/models.py"], {"tests/a/test_x.py"})
    unknown = audit.InstanceAudit("k", True, ["tests/a/test_x.py"], None)
    clean = audit.InstanceAudit("l", True, [], None)
    failed = audit.InstanceAudit("m", False, ["tests/a/models.py"], None)
    arm = {a.instance_id: a for a in (overwritten, live, unknown, clean, failed)}

    assert audit.outcomes(arm, "as_graded") == {
        "i": True,
        "j": True,
        "k": True,
        "l": True,
        "m": False,
    }
    assert audit.outcomes(arm, "strict") == {
        "i": False,
        "j": False,
        "k": False,
        "l": True,
        "m": False,
    }
    # Only the overwritten edit is forgiven; an unknown reset set is read on the conservative side.
    assert audit.outcomes(arm, "harness_aware") == {
        "i": True,
        "j": False,
        "k": False,
        "l": True,
        "m": False,
    }


def _fake_run(root: Path, run: str, arm: str, resolved: list[str], patches: dict[str, str]) -> None:
    d = root / run
    d.mkdir(parents=True, exist_ok=True)
    (d / f"predictions_{arm}.jsonl").write_text(
        "".join(
            json.dumps({"instance_id": i, "model_patch": p}) + "\n" for i, p in patches.items()
        ),
        encoding="utf-8",
    )
    (d / f"report_{arm}.json").write_text(json.dumps({"resolved_ids": resolved}), encoding="utf-8")


def test_an_arm_is_read_from_its_predictions_its_report_and_its_eval_logs(tmp_path: Path):
    audit = _audit()
    _fake_run(
        tmp_path,
        "r",
        "t",
        ["a", "b"],
        {"a": _patch("django/x.py", "tests/q/test_x.py"), "b": _patch("django/y.py"), "c": ""},
    )
    logs = tmp_path / "r" / "logs" / "t" / "model" / "a"
    logs.mkdir(parents=True)
    (logs / "eval.sh").write_text("git checkout abcdef1 tests/q/test_x.py\n", encoding="utf-8")

    got = audit.audit_arm(
        audit.Arm("r/t", "r", "predictions_t.jsonl", "report_t.json", "logs/t"), tmp_path
    )

    assert (
        got["a"].resolved
        and got["a"].test_files == ["tests/q/test_x.py"]
        and got["a"].live_test_edits == []
    )
    assert got["b"].resolved and not got["b"].touches_tests
    assert not got["c"].resolved and got["c"].reset is None


def test_a_report_that_resolves_an_instance_nobody_predicted_aborts(tmp_path: Path):
    audit = _audit()
    _fake_run(tmp_path, "r", "t", ["ghost"], {"a": ""})
    with pytest.raises(SystemExit, match="no prediction"):
        audit.audit_arm(
            audit.Arm("r/t", "r", "predictions_t.jsonl", "report_t.json", "logs/t"), tmp_path
        )


@needs_results
def test_the_raw_reports_reproduce_every_published_delta_and_ci():
    """The §2aa control: the readings are only worth reading if the as-graded one IS the table."""
    audit = _audit()
    audited = {key: audit.audit_arm(arm) for key, arm in audit.ARMS.items()}
    assert audit.check_control(audited) == []


@needs_results
def test_a_control_that_does_not_reproduce_is_reported_not_passed():
    audit = _audit()
    audited = {key: audit.audit_arm(arm) for key, arm in audit.ARMS.items()}
    # Flip one run-3 scaffold+gate resolution: the published run-3 and pooled rows must both fail.
    flipped = next(a for a in audited["run3/treatment_diff"].values() if a.resolved)
    flipped.resolved = False
    failures = audit.check_control(audited)
    assert any(f.startswith("run 3:") for f in failures)
    assert any(f.startswith("pooled:") for f in failures)


@needs_results
def test_the_test_editing_resolutions_the_critic_listed_are_the_ones_found():
    """Study 30's critic listed five; the audit must find exactly those, no more and no fewer."""
    audit = _audit()
    found = {
        (key, iid)
        for key, arm in audit.ARMS.items()
        for iid, a in audit.audit_arm(arm).items()
        if a.resolved and a.touches_tests
    }
    assert found == {
        ("run3/baseline", "django__django-13821"),
        ("run3/baseline", "django__django-14373"),
        ("run3/treatment_diff", "django__django-12741"),
        ("run4/treatment", "django__django-12741"),
        ("run4/treatment", "django__django-14373"),
    }
