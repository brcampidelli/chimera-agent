"""The coverage checklist graded prose too, and deleted the file that satisfied the requirement.

Found by driving the installed rc40 — that is, by testing the release that had just fixed the same
blindness one gate over. A run with `--gen-tests` on a machine with no pytest:

    requirement:  "somar(a, b) devolve a + b"
    diff:         +def somar(a, b):
                  +    return a + b
    feedback:     "Requirements not covered: - somar(a, b) devolve a + b"
    receipt:      verified: true · reverted: true · evidence: diff+manager

Three things had to line up, and they did. The spec-test verifier **abstained**, correctly, because
pytest was not installed. Abstention demotes the attempt to the no-verifier path — which is what
makes the coverage checklist the decisive gate. And that gate was still reading the answer's prose,
so it did not see the two lines that met the requirement, and verify-or-revert removed them.

The reviewer had been given the diff one release earlier. This gate had not. The fix is the same
evidence, on the same terms: it describes what the answer is a claim about, and it cannot add a
requirement — the list stays the one the person kept.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core import AutonomousAgent, AutonomousConfig, WorkspaceGuard
from chimera.core.agent import AgentResult
from chimera.core.checklist import Requirement, RequirementChecklist
from chimera.core.supervisor import Review


class _Backend:
    """Records the prompt it was graded with, and answers however the case needs."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, messages: Any, model: Any = None, **kwargs: Any) -> Any:
        self.prompts.append(messages[-1].content)

        class _R:
            content = self.reply

        return _R()


class _Worker:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        (self.workspace / "soma.py").write_text(
            "def somar(a, b):\n    return a + b\n", encoding="utf-8"
        )
        return AgentResult(answer="Pronto.", steps=1, stopped_reason="final")


class _Approving:
    def review(self, task: str, answer: str, context: str) -> Review:
        return Review(approved=True)


# --------------------------------------------------------------------------- the grader itself


def test_the_diff_reaches_the_grader() -> None:
    """Mechanism. The prompt is what the model reads, so this is where the evidence is or is not."""
    backend = _Backend('{"items": [{"text": "somar(a, b) devolve a + b", "met": true}]}')
    checklist = RequirementChecklist(backend)  # type: ignore[arg-type]

    checklist.grade(
        "build it",
        "Pronto.",
        [Requirement(text="somar(a, b) devolve a + b")],
        evidence="<<what-this-attempt-changed-on-disk>>\n+def somar(a, b):\n<<end>>",
    )
    assert "def somar" in backend.prompts[-1], "the grader still sees only the prose"


def test_without_evidence_the_prompt_is_byte_identical_to_before() -> None:
    """The control, and asserted on the whole string rather than on the absence of a heading.

    A sabotage run proved the weaker version worthless: making the append unconditional passed it,
    because appending an empty string adds no heading — only two blank lines. Harmless there,
    but the assertion was not measuring what it claimed to, and the next change might not be.
    """
    backend = _Backend('{"items": [{"text": "x", "met": true}]}')
    RequirementChecklist(backend).grade("t", "a", [Requirement(text="x")])  # type: ignore[arg-type]
    assert backend.prompts[-1] == "Requirements:\n- [do] x\n\n<<answer>>\na\n<<end-answer>>"


def test_the_grader_is_told_the_changed_files_count_as_evidence() -> None:
    """The instruction is half the fix, and a fake backend cannot notice it changing.

    The old system prompt said to judge *only what the answer actually shows* — which is exactly
    the rule that made a satisfied requirement read as unmet. Handing the model a diff while still
    telling it to ignore everything but the prose would have shipped as a fix and changed nothing.
    Pinned as a clause, not as prose: the wording may move, the permission may not.
    """
    from chimera.core.checklist import _GRADE_SYSTEM

    assert "changed files is met even when the answer does not restate it" in _GRADE_SYSTEM
    assert "only what the answer actually shows" not in _GRADE_SYSTEM


def test_the_grader_still_reports_a_genuine_miss() -> None:
    """The control that matters: showing the work must not become a way of passing everything."""
    backend = _Backend('{"items": [{"text": "x", "met": false}]}')
    misses = RequirementChecklist(backend).grade(  # type: ignore[arg-type]
        "t", "a", [Requirement(text="x")], evidence="<<what-this-attempt-changed-on-disk>>\n<<end>>"
    )
    assert misses == ["x"]


# --------------------------------------------------------------------------- the wiring


class _GradesOnProseOnly:
    """A grader that marks the requirement met only if it can see the code — which is exactly what
    the real one was being asked to do without being shown it."""

    def __init__(self) -> None:
        self.evidence_seen = ""

    def extract(self, task: str) -> list[Requirement]:
        return [Requirement(text="somar(a, b) devolve a + b")]

    def grade(
        self,
        task: str,
        answer: str,
        requirements: list[Requirement],
        *,
        evidence: str = "",
    ) -> list[str]:
        self.evidence_seen = evidence
        return [] if "def somar" in (answer + evidence) else ["somar(a, b) devolve a + b"]


def test_the_work_survives_a_requirement_its_diff_satisfies(tmp_path: Path) -> None:
    """Mechanism to wiring, and the rc40 case end to end: the answer says only "Pronto." and the
    requirement is met by the file. Before, the attempt failed here and the file was reverted."""
    checklist = _GradesOnProseOnly()
    result = AutonomousAgent(
        worker=_Worker(tmp_path),
        manager=_Approving(),
        planner=None,
        verifier=None,
        guard=WorkspaceGuard(tmp_path),
        config=AutonomousConfig(max_attempts=1, use_planner=False),
        checklist=checklist,  # type: ignore[arg-type]
    ).run("crie soma.py com somar(a, b)")

    assert "def somar" in checklist.evidence_seen, "the loop never handed the grader the diff"
    assert result.success is True
    assert (tmp_path / "soma.py").is_file(), "the file that met the requirement was reverted again"
