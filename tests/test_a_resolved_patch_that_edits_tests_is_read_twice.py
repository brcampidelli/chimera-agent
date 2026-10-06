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
import re
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


def test_a_plain_unified_diff_without_a_git_header_still_names_its_test_file():
    """`git apply` takes a `---`/`+++` diff with no `diff --git` line; its test edit must not vanish."""
    audit = _audit()
    plain = (
        "--- a/tests/x/test_y.py\t2026-10-05 10:00:00\n+++ b/tests/x/test_y.py\t2026-10-05 10:01:00\n"
        "@@ -1 +1 @@\n-a\n+b\n"
        "--- /dev/null\r\n+++ b/django/new.py\r\n@@ -0,0 +1 @@\r\n+x\r\n"
    )
    assert audit.edited_files(plain) == ["tests/x/test_y.py", "django/new.py"]
    assert any(audit.is_test_path(p) for p in audit.edited_files(plain))


def test_a_file_header_the_parser_cannot_read_is_refused_not_skipped():
    audit = _audit()
    with pytest.raises(ValueError, match="'\\+\\+\\+' file headers"):
        audit.edited_files("+++ b/tests/x/test_y.py\n@@ -1 +1 @@\n-a\n+b\n")
    with pytest.raises(ValueError, match="'\\+\\+\\+' file headers"):
        audit.edited_files("--- a/tests/my test.py\n+++ b/tests/my test.py\n@@ -1 +1 @@\n-a\n+b\n")


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


def test_a_header_written_with_crlf_is_still_read():
    """A Windows-written patch ends `diff --git` in CRLF; unread, its test edit would pass as clean."""
    audit = _audit()
    crlf = _patch("django/a.py", "tests/x/test_y.py").replace("\n", "\r\n")
    assert audit.edited_files(crlf) == ["django/a.py", "tests/x/test_y.py"]


@pytest.mark.parametrize(
    "header",
    [
        # git C-quotes a non-ASCII path; the regex for bare paths cannot read it.
        r'diff --git "a/tests/t\303\251.py" "b/tests/t\303\251.py"' + "\n",
        "diff --git a/tests/my test.py b/tests/my test.py\n",
    ],
)
def test_a_diff_header_that_cannot_be_parsed_refuses_rather_than_reading_clean(header: str):
    audit = _audit()
    with pytest.raises(ValueError, match="could not be parsed"):
        audit.edited_files(_patch("django/a.py") + header)


def test_an_arm_with_an_unparseable_patch_aborts_naming_the_instance(tmp_path: Path):
    audit = _audit()
    quoted = r'diff --git "a/tests/t\303\251.py" "b/tests/t\303\251.py"' + "\n"
    _fake_run(tmp_path, "r", "t", ["a"], {"a": quoted})
    with pytest.raises(SystemExit, match="r/t a: .*cannot be audited"):
        audit.audit_arm(
            audit.Arm("r/t", "r", "predictions_t.jsonl", "report_t.json", "logs/t"), tmp_path
        )


def test_a_resolved_instance_without_an_eval_sh_is_listed_not_buried(tmp_path: Path):
    """Amendment 6: a missing `eval.sh` is reported as such — for every resolved instance."""
    audit = _audit()
    _fake_run(
        tmp_path,
        "r",
        "t",
        ["a", "b", "c"],
        {"a": _patch("tests/q/test_x.py"), "b": _patch("django/y.py"), "c": "", "d": ""},
    )
    logs = tmp_path / "r" / "logs" / "t" / "model" / "c"
    logs.mkdir(parents=True)
    # A log whose script has no checkout line is "unknown reset", NOT "no eval.sh".
    (logs / "eval.sh").write_text("git status\n", encoding="utf-8")

    got = audit.summarize_arm(
        audit.audit_arm(
            audit.Arm("r/t", "r", "predictions_t.jsonl", "report_t.json", "logs/t"), tmp_path
        )
    )

    # "d" has no eval.sh either but was not resolved, so it cannot have passed for any reason.
    assert got["resolved_without_eval_sh"] == ["a", "b"]
    assert got["instances"]["a"]["reset_by_harness"] is None


def test_two_arms_over_different_instances_are_never_paired():
    """zip() over two sorted, different id sets would pair strangers and print a plausible Δ."""
    audit = _audit()
    left = {"x": audit.InstanceAudit("x", True), "y": audit.InstanceAudit("y", False)}
    right = {"x": audit.InstanceAudit("x", True), "z": audit.InstanceAudit("z", True)}
    with pytest.raises(SystemExit, match="cannot be paired"):
        audit.compare({"b": left, "t": right}, ["b"], ["t"], "as_graded")


def _pct(cell: str) -> float:
    return round(float(cell.replace("−", "-").strip("*% ")) / 100, 3)


def test_the_control_constants_are_the_table_results_md_publishes():
    """PUBLISHED is a hand copy; the control is only a control if the copy IS the page.

    Read from the page itself: the five table rows, and run 4's gate-vs-scaffold line, which RESULTS.md
    prints in the run-4 block rather than in the table.
    """
    audit = _audit()
    text = (_AUDIT.parent / "RESULTS.md").read_text(encoding="utf-8")
    table_label = {
        "run 1": "1 (",
        "run 2": "2 (",
        "run 3": "3 (replication)",
        "pooled": "pooled (secondary)",
        "run 4 scaffold vs baseline": "4 (attribution)",
    }
    rows = [
        [c.strip() for c in line.strip().strip("|").split("|")]
        for line in text.splitlines()[:20]
        if line.startswith("| ") and "%" in line
    ]
    page: dict[str, tuple[float, tuple[float, float]]] = {}
    for label, prefix in table_label.items():
        row = next(r for r in rows if r[0].strip("*").startswith(prefix))
        lo, hi = row[5].strip("*[] ").split(",")
        page[label] = (_pct(row[4]), (_pct(lo), _pct(hi)))
    gate = re.search(
        r"gate\s+vs scaffold :\s+([+-]\d+\.\d)%\s+95% CI \[([+-]?\d+\.\d)%, ([+-]?\d+\.\d)%\]", text
    )
    assert gate is not None, "RESULTS.md no longer prints run 4's gate-vs-scaffold line"
    page["run 4 gate vs scaffold"] = (_pct(gate[1]), (_pct(gate[2]), _pct(gate[3])))

    assert page == {label: (delta, ci) for label, (_, _, delta, ci) in audit.PUBLISHED.items()}


_AUDIT_ANCHOR = "audit-does-the-lift-rest-on-patches-that-edited-tests-study-30-s30-35"


_LANGS = ["en", "de", "es", "fr", "it", "ja", "pl", "pt", "ru", "zh"]
# README suffixes differ from the docs' folder names for two languages (see test_readme_langbar).
_README = {"en": "README.md", "pt": "README.pt-BR.md", "zh": "README.zh-CN.md"}


@pytest.mark.parametrize("lang", _LANGS)
def test_the_benchmarks_page_says_the_lift_is_not_yet_read_under_stronger_tests(lang: str):
    """Amendment 6: until the dynamic gradings run, the lift is quoted with that caveat beside it.

    `docs/benchmarks.md` and its nine translations republish the table, so the caveat sits under the
    table there too, linking to the audit section, not only in RESULTS.md.
    """
    root = _AUDIT.parents[2]
    page = root / "docs" / ("benchmarks.md" if lang == "en" else f"i18n/{lang}/benchmarks.md")
    lines = page.read_text(encoding="utf-8").splitlines()
    table_end = next(i for i, ln in enumerate(lines) if ln.startswith("| 4"))
    caveat = lines[table_end + 2]
    assert caveat.startswith("> ") and f"RESULTS.md#{_AUDIT_ANCHOR})" in caveat, caveat
    if lang == "en":
        assert "has not yet been read under stronger tests" in caveat


@pytest.mark.parametrize("lang", _LANGS)
def test_the_readme_says_the_lift_is_not_yet_read_under_stronger_tests(lang: str):
    """The README republishes the same table under "the strongest external evidence" — and it is the
    page most readers see. A caveat that lives only in docs/ is a retraction published with LESS
    prominence than the claim, which AGENTS.md forbids. Read inside the SWE-bench item itself, from
    its table to the next bullet, so a caveat moved to another section does not count.
    """
    root = _AUDIT.parents[2]
    lines = (root / _README.get(lang, f"README.{lang}.md")).read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("  | **3"))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("- **"))
    caveats = [
        ln
        for ln in lines[start:end]
        if ln.startswith("  ⚠️ ") and f"(bench/swe_bench/RESULTS.md#{_AUDIT_ANCHOR})" in ln
    ]
    assert len(caveats) == 1, lines[start:end]
    if lang == "en":
        assert "has not yet been read under stronger tests" in caveats[0]


def test_the_snapshot_the_app_serves_says_the_lift_is_not_yet_read_under_stronger_tests():
    """The desktop app's benchmark screen reads the shipped snapshot, not the markdown pages."""
    snapshot = json.loads(
        (_AUDIT.parents[2] / "chimera" / "_benchmark_snapshot.json").read_text(encoding="utf-8")
    )
    swe = [e for e in snapshot["external"] if e["benchmark"].startswith("SWE-bench Verified")]
    assert len(swe) == 1
    assert "S30-35" in swe[0]["note"]
    assert "has not yet been read under stronger tests" in swe[0]["note"]


def test_the_anchor_the_pages_link_to_is_the_audit_heading():
    heading = next(
        ln[3:]
        for ln in (_AUDIT.parent / "RESULTS.md").read_text(encoding="utf-8").splitlines()
        if ln.startswith("## Audit:")
    )
    slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
    assert slug == _AUDIT_ANCHOR
