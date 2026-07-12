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


def test_max_iterations_survives_resume_without_a_config(tmp_path: Path) -> None:
    # The rail must be DURABLE: resuming (as the CLI does — no config, rebuilt from disk) must keep
    # the max_iterations set at start, not silently reset to the default 20.
    reqs = [{"id": "r1", "check": "contains", "target": "NEVER", "text": "never"}]

    class NoopSolve(FakeSolve):
        def run(self, card: KanbanCard) -> LaneResult:
            self.ran.append(str(card.metadata["requirement_id"]))
            return LaneResult(success=True, answer="claimed done, changed nothing")

    solve = NoopSolve(tmp_path / "ws", {})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False, max_iterations=1)
    proj.run()
    assert proj.state.iterations == 1  # tripped the rail at 1
    pid, home = proj.state.id, tmp_path / "home"

    # Resume WITHOUT a config (the fixed CLI path): the rail is rebuilt from durable state.
    resumed = ProjectOrchestrator.load(home, pid, solve_card=solve)
    assert resumed.config.max_iterations == 1  # not the default 20
    resumed.run()
    assert resumed.state.iterations == 1  # still capped — did not run 19 more past the user's limit


def test_start_rejects_spec_with_no_requirements(tmp_path: Path) -> None:
    # A misparsed/empty `requirements:` would make check_drift report `aligned=True` (nothing to
    # flip it false) → a vacuous "done" with zero verification. Start must refuse it.
    import pytest

    solve = FakeSolve(tmp_path / "ws", {})
    with pytest.raises(ValueError, match="no required requirements"):
        _project(tmp_path, [], solve, require_plan_approval=False)


def test_project_state_save_is_atomic(tmp_path: Path) -> None:
    reqs = [{"id": "r1", "check": "contains", "target": "HELLO", "text": "has HELLO"}]
    proj = _project(tmp_path, reqs, FakeSolve(tmp_path / "ws", {"r1": "HELLO"}),
                    require_plan_approval=False)
    p = proj.state_path
    assert p.exists()
    assert not p.with_suffix(p.suffix + ".tmp").exists()  # no temp left behind
    assert ProjectState.load(p).id == proj.state.id  # round-trips


def test_orphaned_doing_card_is_recovered_on_resume(tmp_path: Path) -> None:
    # A card stuck in `doing` (crash debris) must be returned to backlog and retried, not orphaned
    # into a permanent "no ready card" escalation.
    reqs = [{"id": "r1", "check": "contains", "target": "HELLO", "text": "has HELLO"}]
    solve = FakeSolve(tmp_path / "ws", {"r1": "HELLO"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)
    # Simulate the crash: a card for r1 left mid-work in `doing`.
    card = proj.board.add(title="[r1]", action="do r1", lane="solve", verify="x")
    card.metadata["requirement_id"] = "r1"
    card.metadata["depends_on"] = []
    proj.board.move(card.id, "doing")

    state = proj.run()
    assert state.status == "done"  # the orphan was recovered and worked, not stranded
    assert solve.ran == ["r1"]


def test_deny_missing_card_does_not_crash(tmp_path: Path) -> None:
    # The pending id lives in project.json, not the board; if the card is gone from the board,
    # deny must escalate cleanly rather than raise KeyError.
    reqs = [{"id": "r1", "check": "contains", "target": "SHIP", "text": "deploy", "risk": "high"}]
    solve = FakeSolve(tmp_path / "ws", {"r1": "SHIP"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)
    state = proj.run()
    card_id = state.pending_card_id
    assert card_id is not None
    proj.board.remove(card_id)  # card vanishes from the board

    proj.deny_card(card_id)  # must not raise
    assert proj.state.status == "escalated"


def test_required_requirement_depending_on_optional_gets_worked(tmp_path: Path) -> None:
    # A required requirement that depends on an unsatisfied OPTIONAL one must still progress: the
    # optional dependency has to be worked, or the required one is blocked forever.
    reqs = [
        {"id": "r_opt", "check": "contains", "target": "OPT", "text": "opt", "required": False},
        {"id": "r_main", "check": "contains", "target": "MAIN", "text": "main",
         "depends_on": ["r_opt"]},
    ]
    solve = FakeSolve(tmp_path / "ws", {"r_opt": "OPT", "r_main": "MAIN"})
    proj = _project(tmp_path, reqs, solve, require_plan_approval=False)

    state = proj.run()
    assert state.status == "done"
    assert solve.ran.index("r_opt") < solve.ran.index("r_main")  # dependency worked first
