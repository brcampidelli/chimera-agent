"""M19 Track B: the ProjectOrchestrator drives a project to spec-alignment, offline.

No model or network: a FakeSolve lane 'does the work' by writing the requirement's target text
into the workspace, and the orchestrator's OWN drift check (the real governance gate) accepts it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from chimera.kanban.dispatch import LaneResult
from chimera.kanban.models import KanbanCard
from chimera.orchestration.project import ProjectConfig, ProjectOrchestrator, ProjectState


def _spec_file(tmp_path: Path, requirements: list[dict[str, Any]]) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(
        yaml.safe_dump({"name": "demo", "requirements": requirements}), encoding="utf-8"
    )
    return path


class FakeSolve:
    """A lane that satisfies a card's requirement by writing its target into the workspace."""

    def __init__(self, ws: Path, targets: dict[str, str], *, fail: tuple[str, ...] = ()) -> None:
        self.ws = ws
        self.targets = targets
        self.fail = set(fail)
        self.ran: list[str] = []

    def run(self, card: KanbanCard) -> LaneResult:
        rid = str(card.metadata["requirement_id"])
        self.ran.append(rid)
        if rid in self.fail:
            return LaneResult(success=False, answer=f"failed {rid}")
        out = self.ws / "out.txt"
        prev = out.read_text(encoding="utf-8") if out.exists() else ""
        out.write_text(prev + self.targets[rid] + "\n", encoding="utf-8")
        return LaneResult(success=True, answer=f"did {rid}")


def _project(tmp_path: Path, requirements: list[dict[str, Any]], solve: FakeSolve, **cfg: Any):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    spec = _spec_file(tmp_path, requirements)
    return ProjectOrchestrator.start(
        spec, ws, home=home, solve_card=solve, config=ProjectConfig(**cfg)
    )


def test_shipped_project_demo_spec_is_valid() -> None:
    # Guard the flagship example (examples/project_demo/) so a Spec-model change can't silently
    # break the showcased demo. Loads the real file and checks the dependency chain.
    from chimera.governance.drift import load_spec

    spec = load_spec("examples/project_demo/spec.yaml")
    assert spec.name == "temperature-converter"
    ids = [r.id for r in spec.requirements]
    assert ids == ["c_to_f", "f_to_c", "round_trip"]
    by_id = {r.id: r for r in spec.requirements}
    assert by_id["f_to_c"].depends_on == ["c_to_f"]
    assert by_id["round_trip"].depends_on == ["f_to_c"]
    assert by_id["round_trip"].check == "command"


def test_project_runs_to_done_respecting_dependencies(tmp_path: Path) -> None:
    reqs = [
        {"id": "r1", "check": "contains", "target": "HELLO", "text": "has HELLO"},
        {"id": "r2", "check": "contains", "target": "WORLD", "text": "has WORLD",
         "depends_on": ["r1"]},
    ]
    solve = FakeSolve(tmp_path / "ws", {"r1": "HELLO", "r2": "WORLD"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)

    state = proj.run()

    assert state.status == "done"
    assert state.iterations == 2
    assert solve.ran == ["r1", "r2"]  # r2 ran only after its dependency r1 was satisfied


def test_project_pauses_for_plan_approval(tmp_path: Path) -> None:
    reqs = [{"id": "r1", "check": "contains", "target": "HELLO", "text": "has HELLO"}]
    solve = FakeSolve(tmp_path / "ws", {"r1": "HELLO"})
    proj = _project(tmp_path, reqs, solve)  # require_plan_approval defaults True

    state = proj.run()
    assert state.status == "awaiting_approval"
    assert solve.ran == []  # nothing ran before the plan was approved

    proj.approve_plan()
    state = proj.run()
    assert state.status == "done"
    assert solve.ran == ["r1"]


def test_high_risk_card_pauses_until_approved(tmp_path: Path) -> None:
    reqs = [{"id": "r1", "check": "contains", "target": "SHIP", "text": "deploy", "risk": "high"}]
    solve = FakeSolve(tmp_path / "ws", {"r1": "SHIP"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)

    state = proj.run()
    assert state.status == "awaiting_approval"
    assert state.pending_card_id is not None
    assert solve.ran == []  # the high-risk (deploy) card did NOT run without approval

    proj.approve_card(state.pending_card_id)
    state = proj.run()
    assert state.status == "done"
    assert solve.ran == ["r1"]


def test_yes_start_then_resume_approve_card_does_not_reask_plan(tmp_path: Path) -> None:
    # Regression (VPS live test): `start --yes` pauses at a high-risk card; RESUMING with a fresh
    # orchestrator (config recomputed as require_plan_approval = not plan_approved, like the CLI) and
    # approving the card must COMPLETE — not bounce back to "approve the initial plan".
    reqs = [
        {"id": "build", "check": "contains", "target": "BUILT", "text": "build"},
        {"id": "deploy", "check": "contains", "target": "SHIP", "text": "deploy",
         "risk": "high", "depends_on": ["build"]},
    ]
    ws = tmp_path / "ws"
    solve = FakeSolve(ws, {"build": "BUILT", "deploy": "SHIP"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)  # --yes
    state = proj.run()
    assert state.status == "awaiting_approval"
    assert state.pending_card_id is not None
    assert state.plan_approved is True  # the fix: proceeding past the plan gate persisted this
    pid, card_id = state.id, state.pending_card_id
    home = tmp_path / "home"

    # Resume exactly as the CLI does: require_plan_approval = not persisted plan_approved.
    reloaded = ProjectState.load(ProjectOrchestrator.project_dir(home, pid) / "project.json")
    resumed = ProjectOrchestrator.load(
        home, pid, solve_card=solve,
        config=ProjectConfig(require_plan_approval=not reloaded.plan_approved),
    )
    resumed.approve_card(card_id)
    final = resumed.run()
    assert final.status == "done"  # NOT stuck re-asking for plan approval
    assert solve.ran == ["build", "deploy"]


def test_denied_high_risk_card_escalates(tmp_path: Path) -> None:
    reqs = [{"id": "r1", "check": "contains", "target": "SHIP", "text": "deploy", "risk": "high"}]
    solve = FakeSolve(tmp_path / "ws", {"r1": "SHIP"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)

    state = proj.run()
    card_id = state.pending_card_id
    assert card_id is not None
    proj.deny_card(card_id)
    assert proj.state.status == "escalated"
    assert solve.ran == []


def test_deny_wrong_card_is_a_noop(tmp_path: Path) -> None:
    # A stale/typo card id must NOT forcibly escalate a project or lose the real pending card.
    reqs = [{"id": "r1", "check": "contains", "target": "SHIP", "text": "deploy", "risk": "high"}]
    solve = FakeSolve(tmp_path / "ws", {"r1": "SHIP"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)
    state = proj.run()
    assert state.status == "awaiting_approval"
    real_pending = state.pending_card_id

    proj.deny_card("bogus-card-id")  # wrong id
    assert proj.state.status == "awaiting_approval"  # unchanged — not force-escalated
    assert proj.state.pending_card_id == real_pending  # the real pending card is preserved


def test_approve_plan_does_not_resume_while_a_card_is_pending(tmp_path: Path) -> None:
    # A project paused on a high-risk CARD must not be flipped to "running" by approve_plan
    # (which approves the plan gate) — that would write an inconsistent running-but-pending state.
    reqs = [{"id": "r1", "check": "contains", "target": "SHIP", "text": "deploy", "risk": "high"}]
    solve = FakeSolve(tmp_path / "ws", {"r1": "SHIP"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)
    proj.run()
    assert proj.state.status == "awaiting_approval" and proj.state.pending_card_id is not None

    proj.approve_plan()
    assert proj.state.status == "awaiting_approval"  # still paused for the card, not running
    assert proj.state.pending_card_id is not None


def test_failed_card_escalates_to_human(tmp_path: Path) -> None:
    reqs = [{"id": "r1", "check": "contains", "target": "HELLO", "text": "has HELLO"}]
    solve = FakeSolve(tmp_path / "ws", {"r1": "HELLO"}, fail=("r1",))
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)

    state = proj.run()
    # the card failed -> parked in review -> no ready card while the spec is unaligned -> escalate
    assert state.status == "escalated"
    assert "no ready card" in state.note
    assert solve.ran == ["r1"]


def test_max_iterations_rail(tmp_path: Path) -> None:
    # A lane that never actually satisfies the requirement (writes nothing) but reports success:
    # the spec stays unaligned, so the run must stop at max_iterations, not loop forever.
    reqs = [{"id": "r1", "check": "contains", "target": "NEVER", "text": "never"}]

    class NoopSolve(FakeSolve):
        def run(self, card: KanbanCard) -> LaneResult:
            self.ran.append(str(card.metadata["requirement_id"]))
            return LaneResult(success=True, answer="claimed done, changed nothing")

    solve = NoopSolve(tmp_path / "ws", {})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False, max_iterations=3)

    state = proj.run()
    # a "done" card that didn't satisfy the spec is re-created next round, so it keeps trying
    # until the rail trips (the spec — not the lane's say-so — is the acceptance authority).
    assert state.status == "escalated"
    assert "max_iterations" in state.note
    assert proj.state.iterations == 3


def test_project_resumes_from_disk(tmp_path: Path) -> None:
    reqs = [
        {"id": "r1", "check": "contains", "target": "HELLO", "text": "has HELLO"},
        {"id": "r2", "check": "contains", "target": "WORLD", "text": "has WORLD"},
    ]
    ws = tmp_path / "ws"
    solve = FakeSolve(ws, {"r1": "HELLO", "r2": "WORLD"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False, max_iterations=1)
    proj.run()  # one iteration -> r1 done, then max_iterations -> escalated
    pid = proj.state.id
    home = tmp_path / "home"

    # reload with a fresh orchestrator + a higher budget and finish the job
    resumed = ProjectOrchestrator.load(
        home, pid, solve_card=solve, config=ProjectConfig(require_plan_approval=False, max_iterations=10)
    )
    state = resumed.run()
    assert state.status == "done"
