"""`chimera review --effort low|medium|high`: how much checking runs, and where the cut lives.

`bench/review_confidence_cut` selected a cut of 0.8 on the finder's confidence and a second set
did not confirm it, so the registered outcome is: ``medium`` exists with that cut, opt-in, and the
default stays ``high``, today's behaviour. The cut is in the pipeline, between the anchor and the
verifier, and every finding it hides is kept in ``dropped`` with its stage and reason; the finder
is asked the same question at every level.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli import review_cmd
from chimera.cli.main import app
from chimera.config import get_settings
from chimera.review import CautiousVerifier, KeepAll, ReviewerChoice, collect, render_text, review
from chimera.review.effort import DEFAULT_EFFORT, MEDIUM_CONFIDENCE_CUT, confidence_cut, verifies
from tests.review_fakes import FakeBackend, finder_json, finding, repo_with_change

ROOT = Path(__file__).resolve().parents[1]
REVIEWER = ReviewerChoice(
    "openrouter/openai/gpt-6-luna", "openrouter/deepseek/deepseek-v4-flash-0731", "flag"
)
runner = CliRunner()


def _two(low: float = 0.5, high: float = 0.9) -> str:
    return finder_json([finding("calc.py", 11, "P1", "sure of it", high),
                        finding("calc.py", 10, "P2", "unsure of it", low)])


def _medium(tmp_path: Path, reply: str) -> tuple[Any, FakeBackend]:
    backend = FakeBackend(reply)
    report = review(collect(repo_with_change(tmp_path)), backend, REVIEWER,
                    CautiousVerifier(backend, REVIEWER.model),
                    confidence_cut=MEDIUM_CONFIDENCE_CUT, effort="medium")
    return report, backend


def test_medium_hides_a_finding_under_the_cut_before_the_verifier_sees_it(tmp_path: Path) -> None:
    report, backend = _medium(tmp_path, _two())

    assert [f.title for f in report.findings] == ["sure of it"]
    checks = [user for role, user, _ in backend.calls if role == "verifier"]
    assert len(checks) == 1 and "sure of it" in checks[0]
    [hidden] = report.dropped
    assert hidden.title == "unsure of it"
    assert hidden.verdict is not None
    assert (hidden.verdict.stage, hidden.verdict.state) == ("confidence", "dropped")
    assert hidden.verdict.reason == "confidence 0.50 < 0.80"
    assert any("--effort high shows them" in note for note in report.notes)
    assert (report.reviewer.effort, report.reviewer.confidence_cut) == ("medium", 0.8)


def test_a_finding_at_the_cut_is_shown(tmp_path: Path) -> None:
    report, _ = _medium(tmp_path, _two(low=0.8))

    assert len(report.findings) == 2
    assert report.dropped == []
    assert not any("confidence under" in note for note in report.notes)


def test_a_finding_with_no_confidence_is_never_cut(tmp_path: Path) -> None:
    unknown = finding("calc.py", 11, "P1", "no number given")
    del unknown["confidence"]

    report, _ = _medium(tmp_path, finder_json([unknown]))

    [shown] = report.findings
    assert shown.confidence is None and shown.title == "no number given"


def test_a_review_whose_every_finding_is_cut_says_so(tmp_path: Path) -> None:
    report, _ = _medium(tmp_path, finder_json([finding("calc.py", 11, confidence=0.3)]))

    assert report.status == "no_findings"
    text = render_text(report, show_dropped=True)
    assert "1 finding(s) with a confidence under 0.80 were not shown" in text
    assert "confidence: under the confidence cut (confidence 0.30 < 0.80)" in text


def test_high_verifies_every_finding_and_cuts_none(tmp_path: Path) -> None:
    backend = FakeBackend(_two(low=0.1))

    report = review(collect(repo_with_change(tmp_path)), backend, REVIEWER,
                    CautiousVerifier(backend, REVIEWER.model), effort="high")

    assert len(report.findings) == 2
    assert [role for role, _, _ in backend.calls].count("verifier") == 2
    assert report.reviewer.confidence_cut is None


def test_the_levels_are_the_registered_table() -> None:
    assert (verifies("low"), confidence_cut("low")) == (False, None)
    assert (verifies("medium"), confidence_cut("medium")) == (True, 0.8)
    assert (verifies("high"), confidence_cut("high")) == (True, None)


def test_the_default_and_the_cut_are_what_the_bench_decided() -> None:
    results = (ROOT / "bench" / "review_confidence_cut" / "RESULTS.md").read_text(encoding="utf-8")

    assert DEFAULT_EFFORT == "high"
    assert "**The default stays `high`, today's behaviour: no cut.**" in results
    assert MEDIUM_CONFIDENCE_CUT == 0.8
    assert "The rule selected **0.8**" in results


def test_the_finder_is_asked_the_same_question_at_every_level(tmp_path: Path) -> None:
    repo = repo_with_change(tmp_path)
    asked = []
    for cut in (None, MEDIUM_CONFIDENCE_CUT):
        for verifier in ("keep-all", "cautious"):
            backend = FakeBackend(_two())
            check = KeepAll() if verifier == "keep-all" else CautiousVerifier(backend, "m")
            review(collect(repo), backend, REVIEWER, check, confidence_cut=cut)
            asked += [user for role, user, _ in backend.calls if role == "finder"]

    assert len(asked) == 4 and len(set(asked)) == 1


# --- the command -------------------------------------------------------------------------------


@pytest.fixture
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_REVIEW_MODEL", "CHIMERA_DEFAULT_MODEL", "CHIMERA_COST_MODE",
                "CHIMERA_WEAK_MODEL", "CHIMERA_MID_MODEL", "CHIMERA_ORCHESTRATOR_MODEL"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _invoke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *args: str
) -> tuple[int, str, FakeBackend]:
    backend = FakeBackend(_two())
    monkeypatch.setattr(review_cmd, "_backend", lambda: backend)
    result = runner.invoke(app, ["review", "--repo", str(repo_with_change(tmp_path)), *args])
    return result.exit_code, result.stdout, backend


@pytest.mark.usefixtures("_isolated")
def test_the_command_runs_high_when_no_level_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, out, backend = _invoke(monkeypatch, tmp_path, "--json")

    assert code == 0
    data = json.loads(out)
    assert (data["reviewer"]["effort"], data["reviewer"]["confidence_cut"]) == ("high", None)
    assert len(data["findings"]) == 2
    assert [role for role, _, _ in backend.calls].count("verifier") == 2


@pytest.mark.usefixtures("_isolated")
@pytest.mark.parametrize(
    ("args", "effort", "shown", "checks"),
    [(("--effort", "low"), "low", 2, 0), (("--no-verify",), "low", 2, 0),
     (("--effort", "medium"), "medium", 1, 1), (("--effort", "HIGH"), "high", 2, 2)],
)
def test_each_level_runs_its_own_stages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: tuple[str, ...], effort: str,
    shown: int, checks: int,
) -> None:
    code, out, backend = _invoke(monkeypatch, tmp_path, "--json", *args)

    assert code == 0
    data = json.loads(out)
    assert data["reviewer"]["effort"] == effort
    assert len(data["findings"]) == shown
    assert [role for role, _, _ in backend.calls].count("verifier") == checks


@pytest.mark.usefixtures("_isolated")
@pytest.mark.parametrize("args", [("--effort", "max"), ("--no-verify", "--effort", "high")])
def test_a_level_it_cannot_honour_is_refused_before_any_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: tuple[str, ...]
) -> None:
    code, _, backend = _invoke(monkeypatch, tmp_path, *args)

    assert code == 2
    assert backend.calls == []
