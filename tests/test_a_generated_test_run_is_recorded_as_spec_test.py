"""Study 30, S30-23, after review: a verify step on model-written tests says so in the ledger.

With ``--gen-tests`` the verifier is a `SpecTestVerifier`, which had a ``command`` TEMPLATE
(``python -m pytest -q -rA {file}``) and no ``source``. The loop read both off the verifier, so
the ledger event for the case where authorship matters most — tests the model wrote — read
``source=unknown`` with a command that never ran literally, and the integrity rule was handed
``{file}`` as a file name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.checklist import Requirement
from chimera.core.spec_test import SpecTestVerifier
from chimera.governance.ledger import TaintLedger


class _Gen:
    def generate(self, task: str, reqs: Any, *, code_context: str = "") -> str:
        return "def test_x():\n    assert True\n"


class _Idle:
    def run(self, task: str) -> AgentResult:
        return AgentResult(answer="done", steps=1, transcript=[], stopped_reason="done")


def _verifier(ws: Path) -> SpecTestVerifier:
    return SpecTestVerifier(
        _Gen(), "t", [Requirement(text="x", kind="do")], ws  # type: ignore[arg-type]  # stub
    )


def test_a_spec_test_verifier_names_its_source_and_the_command_it_runs(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    assert verifier.source == "spec_test"
    assert verifier.rendered_command == "python -m pytest -q -rA test_chimera_spec.py"


def test_the_ledger_event_of_a_generated_test_run_says_spec_test_and_no_template(
    tmp_path: Path,
) -> None:
    taint = TaintLedger()
    agent = AutonomousAgent(
        _Idle(),  # type: ignore[arg-type]  # structural stand-in for the worker
        verifier=_verifier(tmp_path),
        taint=taint,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    agent._verify()
    (event,) = [e for e in taint.events if e.kind == "verify"]
    assert event.detail.startswith("source=spec_test outcome=")
    assert "{" not in event.ref and event.ref.endswith("test_chimera_spec.py")
