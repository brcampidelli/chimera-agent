"""``chimera solve`` judges its work the way the API does, and says who judged it.

Three things were different between the two surfaces that run the same loop:

* the API reads the project's own test command (`resolve_verify`) and the terminal did not, so a
  bare ``chimera solve "fix it"`` was judged by a model reading the answer where the desktop app
  would have run the tests;
* the Manager reviewed with the model that wrote the patch whenever no role profile separated them
  (the default), so with no verifier the only gate was an author approving its own work;
* a receipt could not say either: ``verify_source`` defaulted to ``user`` even for a run with no
  verifier, and nothing named the judge.

Free: models are fakes and the solve command is stopped before it calls one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chimera.api.runs import build_receipt
from chimera.cli.main import _solve_from_conversation
from chimera.config import get_settings
from chimera.core import AutonomousAgent, AutonomousConfig
from chimera.core.agent import AgentConfig, AgentResult
from chimera.core.supervisor import Review
from chimera.core.verify import VerificationResult


class _Stop(Exception):
    """Raised by the recording AutonomousAgent once it has seen how solve built it."""


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + "test")
    for var in ("CHIMERA_CASCADE", "CHIMERA_AUTO_FUSE", "CHIMERA_REACH", "CHIMERA_GOVERNANCE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("chimera.sandbox._warned", False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- solve builds the same verifier the API would -------------------------------------------------


def _build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs: Any) -> tuple[dict[str, Any], list[Any]]:
    """Run ``solve`` up to the construction of the loop and return what it was built with."""
    built: dict[str, Any] = {}
    configs: list[Any] = []

    class _Worker:
        def __init__(self, _backend: Any, _registry: Any, config: Any = None, **_k: Any) -> None:
            configs.append(config)

    class _Recorder:
        def __init__(self, _worker: Any, **kw: Any) -> None:
            built.update(kw)
            raise _Stop

    monkeypatch.setattr("chimera.core.Agent", _Worker)
    monkeypatch.setattr("chimera.core.AutonomousAgent", _Recorder)
    with pytest.raises(_Stop):
        _solve_from_conversation(
            "fix the parser", workspace=str(tmp_path / "repo"), model=None, write_region=None,
            **kwargs,
        )
    return built, configs


def _repo(tmp_path: Path) -> Path:
    ws = tmp_path / "repo"
    ws.mkdir(exist_ok=True)
    return ws


def test_solve_reads_the_projects_test_command_when_none_was_typed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (_repo(tmp_path) / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    built, _ = _build(monkeypatch, tmp_path)

    verifier = built["verifier"]
    assert verifier is not None and verifier.command == "make test"
    assert built["verify_source"] == "inferred:Makefile"
    # The same gate the API applies: a command read off a file is not "user", so it does not run on
    # the host merely because solve was pointed at the directory.
    assert verifier.source == "inferred"


def test_a_project_with_nothing_to_find_has_no_verifier_and_says_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _repo(tmp_path)
    built, _ = _build(monkeypatch, tmp_path)
    assert built["verifier"] is None
    assert built["verify_source"] == "none"


def test_gen_tests_keeps_the_empty_verifier_slot_it_fills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--gen-tests`` generates the verifier only when there is none; an inferred one would win."""
    from chimera.cli.commands.solve import solve  # noqa: F401 -- ensure the module is importable

    (_repo(tmp_path) / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    built, _ = _build_with_options(monkeypatch, tmp_path, gen_tests=True)
    assert built["verifier"] is None and built["verify_source"] == "none"


def _build_with_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **options: Any
) -> tuple[dict[str, Any], list[Any]]:
    """Like :func:`_build`, but through ``chimera solve`` itself so its flags can be set."""
    from typer.testing import CliRunner

    from chimera.cli.main import app

    built: dict[str, Any] = {}
    configs: list[Any] = []

    class _Worker:
        def __init__(self, _backend: Any, _registry: Any, config: Any = None, **_k: Any) -> None:
            configs.append(config)

    class _Recorder:
        def __init__(self, _worker: Any, **kw: Any) -> None:
            built.update(kw)
            raise _Stop

    monkeypatch.setattr("chimera.core.Agent", _Worker)
    monkeypatch.setattr("chimera.core.AutonomousAgent", _Recorder)
    args = ["solve", "fix the parser", "--workspace", str(tmp_path / "repo")]
    if options.get("gen_tests"):
        args.append("--gen-tests")
    CliRunner().invoke(app, args)
    return built, configs


def test_a_typed_verify_is_still_authorised_by_construction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    _repo(tmp_path)
    built: dict[str, Any] = {}

    class _Worker:
        def __init__(self, *_a: Any, **_k: Any) -> None: ...

    class _Recorder:
        def __init__(self, _worker: Any, **kw: Any) -> None:
            built.update(kw)
            raise _Stop

    monkeypatch.setattr("chimera.core.Agent", _Worker)
    monkeypatch.setattr("chimera.core.AutonomousAgent", _Recorder)
    CliRunner().invoke(
        app, ["solve", "fix it", "--workspace", str(tmp_path / "repo"), "--verify", "pytest -q"]
    )
    assert built["verifier"].command == "pytest -q" and built["verifier"].source == "user"
    assert built["verify_source"] == "user"


def test_the_api_and_solve_share_one_rule() -> None:
    import chimera.api.app as api
    import chimera.core.verify_infer as core

    assert api.resolve_verify is core.resolve_verify
    assert api.verifier_source is core.verifier_source


def test_solve_puts_skills_and_cards_in_the_turn_not_the_system_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """a02-07 as filed (``turn_context`` off in solve) does not reproduce; this keeps it that way."""
    _repo(tmp_path)
    _, configs = _build(monkeypatch, tmp_path)
    assert configs and all(isinstance(c, AgentConfig) and c.turn_context for c in configs)


# --- the loop: a reviewer on the author's own model does not decide ---------------------------


class _Worker:
    def __init__(self, model: str | None = "writer", *, edit: bool = True, ws: Path | None = None) -> None:
        self.config = AgentConfig(model=model)
        self.edit = edit
        self.ws = ws
        self.runs = 0

    def run(self, task: str, **kw: Any) -> AgentResult:
        self.runs += 1
        if self.edit and self.ws is not None:
            (self.ws / f"out{self.runs}.txt").write_text("x", encoding="utf-8")
        return AgentResult(answer="done", steps=1, stopped_reason="done", usd=0.001)


class _Reviewer:
    """A Manager stand-in that always asks for a revision."""

    def __init__(self, model: str | None) -> None:
        self.model = model
        self.backend = object()
        self.calls = 0

    def review(self, task: str, proposed: str, *, context: str = "") -> Review:
        self.calls += 1
        return Review(approved=False, feedback="the parser looks wrong")


def _run(tmp_path: Path, worker: _Worker, reviewer: _Reviewer, **kw: Any) -> Any:
    from chimera.core import WorkspaceGuard

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    worker.ws = ws
    auto = AutonomousAgent(
        worker,
        manager=reviewer,  # type: ignore[arg-type] -- duck-typed reviewer, as the loop only calls .review
        guard=WorkspaceGuard(ws),
        workspace=ws,
        run_log=tmp_path / "runs.jsonl",
        config=AutonomousConfig(max_attempts=2, use_planner=False),
        **kw,
    )
    result = auto.run("fix the parser")
    return auto, result


def test_a_review_by_the_model_that_wrote_the_patch_is_a_note_not_a_veto(tmp_path: Path) -> None:
    worker, reviewer = _Worker("writer"), _Reviewer("writer")
    _, result = _run(tmp_path, worker, reviewer)

    assert reviewer.calls == 1 and worker.runs == 1, "a self-review must not trigger a retry"
    attempt = result.attempts[0]
    assert attempt.success is True
    assert attempt.evaluator == "self"
    assert attempt.evidence == "diff", "the author's approval must not be named as the authority"
    assert "Advisory" in attempt.feedback and "parser looks wrong" in attempt.feedback


def test_a_reviewer_on_another_model_still_decides(tmp_path: Path) -> None:
    worker, reviewer = _Worker("writer"), _Reviewer("someone-else")
    _, result = _run(tmp_path, worker, reviewer)

    assert worker.runs == 2, "an independent REVISE is a veto and buys a retry"
    assert [a.evaluator for a in result.attempts] == ["other", "other"]
    assert result.success is False


def test_a_reviewer_that_cannot_be_compared_is_unspecified_not_independent(tmp_path: Path) -> None:
    _, result = _run(tmp_path, _Worker(None), _Reviewer(None))
    assert result.attempts[0].evaluator == "unspecified"


def test_an_executable_verifier_is_named_as_the_judge(tmp_path: Path) -> None:
    class _Passes:
        command = "make test"
        source = "inferred"

        def verify(self) -> VerificationResult:
            return VerificationResult(True, "ok")

    _, result = _run(tmp_path, _Worker("writer"), _Reviewer("writer"), verifier=_Passes())
    assert result.attempts[0].evaluator == "verifier"


# --- the receipt: 100% of rows carry the judge and where the command came from -----------------


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize(
    ("verify_command", "verify_source", "expected_source"),
    [("make test", "inferred:Makefile", "inferred:Makefile"), (None, "user", "none")],
)
def test_every_receipt_names_the_evaluator_and_the_origin_of_the_check(
    tmp_path: Path, verify_command: str | None, verify_source: str, expected_source: str
) -> None:
    class _V:
        source = "inferred"

        def __init__(self, command: str) -> None:
            self.command = command

        def verify(self) -> VerificationResult:
            return VerificationResult(True, "ok")

    verifier = _V(verify_command) if verify_command else None
    _run(
        tmp_path, _Worker("writer"), _Reviewer("writer"), verifier=verifier,
        verify_source=verify_source,
    )
    rows = _rows(tmp_path / "runs.jsonl")
    assert rows, "the run left no receipt"
    for row in rows:
        assert row["verify_source"] == expected_source
        assert row["attempts"] and all(a["evaluator"] for a in row["attempts"])


def test_the_minimal_fallback_row_also_names_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "chimera.api.runs.build_receipt", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
    )
    _run(tmp_path, _Worker("writer"), _Reviewer("writer"))
    row = _rows(tmp_path / "runs.jsonl")[0]
    assert row["partial"] is True
    assert row["verify_source"] == "none"
    assert row["attempts"][0]["evaluator"] == "self"


def test_a_receipt_written_before_the_field_reads_none() -> None:
    class _A:
        index = 1
        verified = True
        success = True
        reverted = False
        verify_output = ""
        diff_summary = ""
        feedback = ""

    class _R:
        success = True
        paused = False
        answer = ""
        attempts = [_A()]

    receipt = build_receipt(_R(), "t", None, "2026-11-13T00:00:00+00:00")
    assert receipt.attempts[0].evaluator == "none"
