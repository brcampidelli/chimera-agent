"""`chimera review --json` prints one object in a fixed schema, and nothing else on stdout.

Tools read this form, so it is a contract: ``schema`` names its version, every model forbids fields
it does not declare, and the command writes its progress to stderr so stdout parses as it is.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from chimera.cli import review_cmd
from chimera.cli.main import app
from chimera.config import get_settings
from chimera.review.report import SCHEMA, ReviewReport
from tests.review_fakes import FakeBackend, finder_json, finding, repo_with_change

runner = CliRunner()

FINDING_KEYS = {"id", "priority", "file", "line", "title", "evidence", "consequence", "confidence",
                "verdict"}
REPORT_KEYS = {"schema", "experimental", "status", "base", "base_label", "target", "files_changed",
               "findings", "dropped", "residual_risks", "untested_paths", "not_reviewed",
               "untracked_skipped", "notes", "reviewer", "usage"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_REVIEW_MODEL", "CHIMERA_DEFAULT_MODEL", "CHIMERA_COST_MODE",
                "CHIMERA_WEAK_MODEL", "CHIMERA_MID_MODEL", "CHIMERA_ORCHESTRATOR_MODEL"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _run(monkeypatch: pytest.MonkeyPatch, repo: Path, backend: FakeBackend, *args: str) -> str:
    monkeypatch.setattr(review_cmd, "_backend", lambda: backend)
    result = runner.invoke(app, ["review", "--repo", str(repo), *args])
    assert result.exit_code == 0, result.output
    return result.stdout


def test_the_json_form_is_one_object_in_the_declared_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = FakeBackend(finder_json([finding("calc.py", 11, "P1", "divides by n - 1", 0.9)]))

    data = json.loads(_run(monkeypatch, repo_with_change(tmp_path), backend, "--json"))

    assert set(data) == REPORT_KEYS
    assert data["schema"] == SCHEMA == "chimera.review/1"
    assert data["experimental"] is True
    assert data["status"] == "findings"
    [only] = data["findings"]
    assert set(only) == FINDING_KEYS
    assert (only["id"], only["priority"], only["file"], only["line"]) == ("F1", "P1", "calc.py", 11)
    assert set(only["verdict"]) == {"state", "label", "reason", "stage"}
    assert ReviewReport.model_validate(data).to_json() == json.dumps(data, indent=2)


def test_a_field_the_schema_does_not_declare_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = FakeBackend(finder_json([finding("calc.py", 11)]))
    data = json.loads(_run(monkeypatch, repo_with_change(tmp_path), backend, "--json"))

    data["findings"][0]["severity"] = "high"

    with pytest.raises(ValidationError):
        ReviewReport.model_validate(data)


def test_the_human_form_says_it_is_experimental_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = FakeBackend(finder_json([finding("calc.py", 11, "P1", "divides by n - 1")]))

    text = _run(monkeypatch, repo_with_change(tmp_path), backend)

    assert text.splitlines()[0].startswith("Review (experimental): 1 finding(s)")
    assert "P1  calc.py:11  divides by n - 1" in text
