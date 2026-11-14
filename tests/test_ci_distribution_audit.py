"""CI audits the distributed extras and blocks actionable CVEs without hiding scanner failures."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import ci_pip_audit

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github/workflows/ci.yml"


def _report(fixes: list[str] | None = None) -> dict[str, object]:
    vulns = [] if fixes is None else [{"id": "CVE-TEST", "fix_versions": fixes}]
    return {"dependencies": [{"name": "example", "version": "1.0", "vulns": vulns}]}


@pytest.mark.parametrize(
    ("fixes", "status", "expected", "message"),
    [
        (None, 0, 0, "0 fixable"),
        ([], 1, 0, "ADVISORY: example==1.0 CVE-TEST — no published fix"),
        (["1.1"], 1, 1, "BLOCK: example==1.0 CVE-TEST — fixed in 1.1"),
    ],
)
def test_audit_distinguishes_clean_unfixable_and_fixable(
    fixes: list[str] | None, status: int, expected: int, message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ci_pip_audit.evaluate(_report(fixes), status) == expected
    assert message in capsys.readouterr().out


def test_an_unfixed_advisory_cannot_hide_a_fixable_one(capsys: pytest.CaptureFixture[str]) -> None:
    report = {"dependencies": [{"name": "example", "version": "1.0", "vulns": [
        {"id": "CVE-UNFIXED", "fix_versions": []},
        {"id": "CVE-FIXED", "fix_versions": ["1.1"]},
    ]}]}
    assert ci_pip_audit.evaluate(report, 1) == 1
    output = capsys.readouterr().out
    assert "ADVISORY: example==1.0 CVE-UNFIXED" in output
    assert "BLOCK: example==1.0 CVE-FIXED" in output


@pytest.mark.parametrize(
    ("report", "status"),
    [
        ({}, 0),
        ({"dependencies": []}, 0),
        ({"dependencies": [{"name": "example", "skip_reason": "not found"}]}, 1),
        ({"dependencies": [{"name": "example", "version": "1.0"}]}, 0),
        ({"dependencies": [{"name": "example", "version": "1.0", "vulns": [
            {"id": "CVE-TEST"},
        ]}]}, 1),
        ({"dependencies": [{"name": "example", "version": "1.0", "vulns": [
            {"id": "CVE-TEST", "fix_versions": "1.1"},
        ]}]}, 1),
        (_report(), 1),
        (_report([]), 0),
        (_report(), 2),
    ],
)
def test_incomplete_or_failed_audits_cannot_pass(report: object, status: int) -> None:
    with pytest.raises(ValueError):
        ci_pip_audit.evaluate(report, status)


@pytest.mark.parametrize(("stdout", "status", "expected"), [
    (json.dumps(_report()), 0, 0),
    (json.dumps(_report([])), 1, 0),
    (json.dumps(_report(["1.1"])), 1, 1),
    ("", 1, 1),
    ("not json", 0, 1),
    (json.dumps(_report()), 2, 1),
])
def test_scanner_invocation_never_resolves_or_installs_the_export(
    monkeypatch: pytest.MonkeyPatch, stdout: str, status: int, expected: int,
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[:4] == ["uvx", "pip-audit", "--requirement", "requirements.txt"]
        assert {"--strict", "--no-deps", "--disable-pip", "--format", "json"} <= set(command)
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        return subprocess.CompletedProcess(command, status, stdout, "scanner diagnostic\n")

    monkeypatch.setattr(ci_pip_audit.subprocess, "run", run)
    assert ci_pip_audit.main("requirements.txt") == expected


@pytest.mark.parametrize("filename", ["ci.yml", "quality-bench.yml"])
def test_every_ci_uv_sync_and_run_enforces_the_lock(filename: str) -> None:
    workflow = yaml.safe_load((CI.parent / filename).read_text(encoding="utf-8"))
    commands = [
        match.group(0)
        for job in workflow["jobs"].values()
        for step in job["steps"]
        for match in re.finditer(r"\buv (?:sync|run)\b[^\n]*", step.get("run", ""))
    ]
    assert commands
    assert all("--locked" in command.split() for command in commands)


def test_fixable_cves_gate_the_required_verdict_and_audit_every_extra() -> None:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    supply = workflow["jobs"]["supply-chain"]
    assert not supply.get("continue-on-error", False)
    [audit] = [step for step in supply["steps"] if "ci_pip_audit.py" in step.get("run", "")]
    assert not audit.get("continue-on-error", False)
    export = next(line for line in audit["run"].splitlines() if "uv export" in line)
    assert {"--locked", "--all-extras", "--no-emit-project"} <= set(export.split())
    assert "uv lock --check" in [step.get("run") for step in supply["steps"]]
    assert "supply-chain" in workflow["jobs"]["verdict"]["needs"]


def test_full_is_installed_on_all_three_systems_and_both_python_extremes() -> None:
    smoke = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["install-smoke"]
    matrix = smoke["strategy"]["matrix"]
    assert set(matrix["extra"]) == {"desktop", "full"}
    assert set(matrix["os"]) == {"ubuntu-latest", "windows-latest", "macos-latest"}
    assert matrix["python-version"] == ["3.11", "3.13"]
    assert not ({"include", "exclude"} & matrix.keys())
    assert any('python -m pip install ".[${{ matrix.extra }}]"' in step.get("run", "")
               for step in smoke["steps"])


@pytest.mark.parametrize("job_id", ["quality", "quality-windows"])
def test_pytest_label_matches_the_actual_shard_count(job_id: str) -> None:
    job = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"][job_id]
    [step] = [step for step in job["steps"] if "pytest" in step.get("run", "")]
    assert step["name"].endswith(f"of {len(job['strategy']['matrix']['shard'])})")
