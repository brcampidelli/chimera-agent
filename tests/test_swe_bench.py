"""Tests for the SWE-bench Verified-Mini adapter (M14 A2)."""

from __future__ import annotations

from pathlib import Path

from chimera.eval.swe_bench import (
    SWEInstance,
    build_solve_command,
    compare_arms,
    load_instances,
    parse_report,
    report_to_trials,
)


def _instance(**kw: object) -> SWEInstance:
    base = {
        "instance_id": "django__django-1",
        "repo": "django/django",
        "base_commit": "abc123",
        "problem_statement": "Fix the bug in the ORM.",
    }
    base.update(kw)
    return SWEInstance.from_dict(base)


# --- instance parsing --------------------------------------------------------------------


def test_from_dict_reads_test_lists_in_both_shapes() -> None:
    inst = SWEInstance.from_dict(
        {
            "instance_id": "x-1",
            "FAIL_TO_PASS": '["test_a", "test_b"]',  # JSON-encoded string (the dataset form)
            "pass_to_pass": ["test_c"],  # already a list
        }
    )
    assert inst.fail_to_pass == ["test_a", "test_b"]
    assert inst.pass_to_pass == ["test_c"]


def test_load_instances_from_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "mini.jsonl"
    path.write_text(
        '{"instance_id": "a", "problem_statement": "p1"}\n'
        '\n'  # blank line tolerated
        '{"instance_id": "b", "problem_statement": "p2"}\n',
        encoding="utf-8",
    )
    instances = load_instances(path)
    assert [i.instance_id for i in instances] == ["a", "b"]


# --- solve command (treatment arm) -------------------------------------------------------


def test_build_solve_command_includes_issue_and_scaffolding() -> None:
    argv = build_solve_command(_instance(), model="free/model")
    assert argv[:2] == ["chimera", "solve"]
    assert "Fix the bug in the ORM." in argv[2]  # the issue is the task
    assert "--model" in argv and "free/model" in argv
    assert "--checklist" in argv and "--repo-map" in argv  # scaffolding under test


def test_build_solve_command_adds_verify_when_test_cmd_present() -> None:
    argv = build_solve_command(_instance(test_cmd="pytest tests/test_orm.py"), model="m")
    i = argv.index("--verify")
    assert argv[i + 1] == "pytest tests/test_orm.py"  # executable ground truth for verify-or-revert


def test_build_solve_command_omits_verify_without_test_cmd() -> None:
    assert "--verify" not in build_solve_command(_instance(), model="m")


def test_baseline_flags_can_be_empty() -> None:
    argv = build_solve_command(_instance(), model="m", flags=())
    assert "--repo-map" not in argv  # Arm A (model-only) runs without the scaffolding


# --- report parsing ----------------------------------------------------------------------


def test_parse_report_resolved_ids_shape() -> None:
    report = {"resolved_ids": ["a", "b"], "unresolved_ids": ["c"]}
    assert parse_report(report) == {"a": True, "b": True, "c": False}


def test_parse_report_per_instance_shapes() -> None:
    assert parse_report({"a": True, "b": False}) == {"a": True, "b": False}
    assert parse_report({"a": {"resolved": True}, "b": {"resolved": False}}) == {"a": True, "b": False}


def test_report_to_trials_fixes_instance_order_and_missing_is_false() -> None:
    report = {"resolved_ids": ["a", "c"]}
    trials = report_to_trials(report, ["a", "b", "c"])  # b is missing -> unresolved
    assert trials == [True, False, True]


# --- A/B reuse ---------------------------------------------------------------------------


def test_compare_arms_reuses_the_honest_engine() -> None:
    ids = [f"i-{n}" for n in range(30)]
    baseline = {"resolved_ids": ids[:12]}  # 12/30
    treatment = {"resolved_ids": ids[:28]}  # 28/30
    result = compare_arms(baseline, treatment, ids)
    assert result.baseline.n == 30 and result.treatment.n == 30
    assert result.delta > 0
    assert result.significant is True  # a big, real lift clears the CI
    assert result.baseline.name == "model-only" and result.treatment.name == "model+chimera"


def test_compare_arms_small_edge_not_significant() -> None:
    ids = [f"i-{n}" for n in range(20)]
    baseline = {"resolved_ids": ids[:10]}
    treatment = {"resolved_ids": ids[:11]}  # +1 on 20 instances
    assert compare_arms(baseline, treatment, ids).significant is False  # honest: CI includes 0
