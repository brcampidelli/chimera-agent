"""Project orchestrator (M19 Track B): run a project start-to-finish against a Spec.

This stitches pieces that already exist into the one thing the agent stack was missing — a
**project** loop above the single task:

- The drift **Spec** (``governance/drift.py``) is the executable acceptance authority — the ONLY
  "done" signal, so nothing is accepted on a model's say-so.
- A Kanban **board** is the durable task-graph. Dependencies live in ``card.metadata["depends_on"]``
  (requirement ids); a card whose deps aren't satisfied sits in the ``blocked`` column until they are.
- Each unsatisfied requirement becomes a card whose ``verify`` is ``chimera drift <spec> --only <id>``
  — so working the card and passing verify-or-revert maps one-to-one to satisfying that requirement.
- The card is worked by the injected solve lane (which, post-M19-A4, carries the EvolutionContext —
  so running a project feeds the flywheel).

The loop runs until the Spec is aligned, or a rail trips: ``max_iterations``, a **high-risk** card
awaiting human approval (deploy/migration/delete → pause), or **no ready card left while the spec is
still unaligned** (a failed card parked for review → escalate to a human). Every dependency is a
Protocol, so the whole loop is testable without a model or a network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel

from chimera.kanban.board import KanbanBoard
from chimera.kanban.dispatch import LaneResult
from chimera.kanban.models import KanbanCard
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from chimera.governance.drift import DriftReport, Requirement, Spec

_log = get_logger("orchestration.project")

ProjectStatus = Literal[
    "planning",  # created; the initial plan awaits approval (if required)
    "running",  # actively working cards
    "awaiting_approval",  # paused for a human (plan, or a high-risk card)
    "done",  # the spec is aligned
    "escalated",  # a rail tripped (max_iterations, or no ready card with the spec unaligned)
]

# "done" is the only TRULY terminal status — the spec is aligned, nothing more to do. "escalated"
# is a soft rail-stop (max_iterations / needs a human): a fresh run() with a raised budget or a
# fixed workspace re-attempts it, so step() only short-circuits on "done".
_PAUSED: frozenset[str] = frozenset({"done", "escalated", "awaiting_approval"})
_OPEN_COLUMNS: frozenset[str] = frozenset({"backlog", "doing", "blocked", "review"})


def _default_check_drift(spec: Spec, workspace: Path) -> DriftReport:
    """The real drift gate, imported lazily so this module doesn't pull governance at import
    time (governance→core.checkpoint would otherwise close an import cycle through orchestration)."""
    from chimera.governance.drift import check_drift

    return check_drift(spec, workspace)


def _load_spec(path: str | Path) -> Spec:
    from chimera.governance.drift import load_spec

    return load_spec(path)


class SupportsSolveCard(Protocol):
    """A lane that works one card verify-or-revert (the real ``SolveLane`` or a fake)."""

    def run(self, card: KanbanCard) -> LaneResult: ...


@dataclass
class ProjectConfig:
    max_iterations: int = 20
    require_plan_approval: bool = True


class ProjectState(BaseModel):
    """Durable, resumable project state (``home/projects/<id>/project.json``)."""

    id: str
    spec_path: str
    workspace: str
    board_path: str
    status: ProjectStatus = "planning"
    iterations: int = 0
    plan_approved: bool = False
    pending_card_id: str | None = None  # a high-risk card awaiting approval
    note: str = ""

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> ProjectState:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class ProjectOrchestrator:
    """Drive a project against a Spec: gap → card → solve → accept-against-spec → repeat."""

    def __init__(
        self,
        spec: Spec,
        board: KanbanBoard,
        state: ProjectState,
        *,
        solve_card: SupportsSolveCard,
        state_path: Path,
        check_drift_fn: Callable[[Spec, Path], DriftReport] | None = None,
        config: ProjectConfig | None = None,
    ) -> None:
        self.spec = spec
        self.board = board
        self.state = state
        self.solve_card = solve_card
        self.state_path = state_path
        self.check_drift = check_drift_fn or _default_check_drift
        self.config = config or ProjectConfig()
        self._ws = Path(state.workspace).resolve()
        self._spec_abs = Path(state.spec_path).resolve()

    # ---------------------------------------------------------------- factory

    @staticmethod
    def project_dir(home: Path, project_id: str) -> Path:
        return Path(home) / "projects" / project_id

    @classmethod
    def start(
        cls,
        spec_path: str | Path,
        workspace: str | Path,
        *,
        home: Path,
        solve_card: SupportsSolveCard,
        project_id: str | None = None,
        config: ProjectConfig | None = None,
        check_drift_fn: Callable[[Spec, Path], DriftReport] | None = None,
    ) -> ProjectOrchestrator:
        """Create a new project (dirs, board, state) from a spec file."""
        spec = _load_spec(spec_path)
        pid = project_id or uuid.uuid4().hex[:8]
        pdir = cls.project_dir(home, pid)
        pdir.mkdir(parents=True, exist_ok=True)
        board = KanbanBoard(pdir / "board.json")
        state = ProjectState(
            id=pid,
            spec_path=str(Path(spec_path).resolve()),
            workspace=str(Path(workspace).resolve()),
            board_path=str(pdir / "board.json"),
            status="planning",
        )
        state.save(pdir / "project.json")
        return cls(
            spec, board, state,
            solve_card=solve_card, state_path=pdir / "project.json",
            check_drift_fn=check_drift_fn, config=config,
        )

    @classmethod
    def load(
        cls,
        home: Path,
        project_id: str,
        *,
        solve_card: SupportsSolveCard,
        check_drift_fn: Callable[[Spec, Path], DriftReport] | None = None,
        config: ProjectConfig | None = None,
    ) -> ProjectOrchestrator:
        """Reconstruct a project for resume."""
        pdir = cls.project_dir(home, project_id)
        state = ProjectState.load(pdir / "project.json")
        spec = _load_spec(state.spec_path)
        board = KanbanBoard(Path(state.board_path))
        return cls(
            spec, board, state,
            solve_card=solve_card, state_path=pdir / "project.json",
            check_drift_fn=check_drift_fn, config=config,
        )

    # ------------------------------------------------------------- approvals

    def approve_plan(self) -> ProjectState:
        self.state.plan_approved = True
        if self.state.status == "awaiting_approval":
            self.state.status = "running"
        return self._save()

    def approve_card(self, card_id: str) -> ProjectState:
        """Sanction a high-risk card so its next pick runs (deploy/migration/delete)."""
        card = self.board.get(card_id)
        if card is not None:
            card.metadata["approved"] = True
            self.board.save()
        if self.state.pending_card_id == card_id:
            self.state.pending_card_id = None
            self.state.status = "running"
        return self._save()

    def deny_card(self, card_id: str) -> ProjectState:
        """Reject a high-risk card: park it in review and escalate to a human."""
        if self.board.get(card_id) is not None:
            self.board.move(card_id, "review")
        self.state.pending_card_id = None
        self.state.status = "escalated"
        self.state.note = f"high-risk card {card_id} denied by human"
        return self._save()

    # ------------------------------------------------------------------ loop

    def step(self) -> ProjectState:
        """Advance one iteration: check the spec, sync cards, run at most ONE ready card."""
        if self.state.status == "done":
            return self.state
        report = self.check_drift(self.spec, self._ws)
        if report.aligned:
            return self._set("done", "spec aligned — project complete")
        self._sync_cards(report)
        satisfied = {r.id for r in report.results if r.satisfied}
        self._reconcile_blocked(satisfied)
        if self.config.require_plan_approval and not self.state.plan_approved:
            return self._set("awaiting_approval", "approve the initial plan to begin")
        if self.state.iterations >= self.config.max_iterations:
            return self._set("escalated", f"max_iterations ({self.config.max_iterations}) reached")
        card = self._next_ready_card(satisfied)
        if card is None:
            return self._set(
                "escalated", "no ready card while the spec is still unaligned — needs a human"
            )
        if self._is_high_risk(card) and not card.metadata.get("approved"):
            self.state.pending_card_id = card.id
            return self._set("awaiting_approval", f"high-risk card {card.id} needs approval")
        self._work(card)
        self.state.iterations += 1
        self.state.pending_card_id = None
        self.state.status = "running"
        return self._save()

    def run(self) -> ProjectState:
        """Loop ``step`` until the spec aligns or a rail stops the run (never past a pause).

        An explicit ``run`` re-attempts a soft rail-stop: a project that previously ``escalated`` on
        ``max_iterations`` restarts here (with a fresh budget or a fixed workspace). ``done`` is
        terminal and returns immediately.
        """
        if self.state.status == "done":
            return self.state
        if self.state.status == "escalated":
            self.state.status = "running"
        # A hard backstop above max_iterations in case a step neither advances nor stops.
        guard = self.config.max_iterations + len(self.spec.requirements) + 5
        while guard > 0:
            guard -= 1
            self.step()
            if self.state.status in _PAUSED:
                break
        return self.state

    def status(self) -> ProjectState:
        return self.state

    # ------------------------------------------------------------- internals

    def _gaps(self, report: DriftReport) -> list[str]:
        """Ids of REQUIRED requirements that are currently unsatisfied."""
        required = {r.id for r in self.spec.requirements if r.required}
        return [r.id for r in report.results if not r.satisfied and r.id in required]

    def _requirement(self, req_id: str) -> Requirement | None:
        return next((r for r in self.spec.requirements if r.id == req_id), None)

    def _open_card_for(self, req_id: str) -> KanbanCard | None:
        for card in self.board.cards():
            if card.metadata.get("requirement_id") == req_id and card.column in _OPEN_COLUMNS:
                return card
        return None

    def _sync_cards(self, report: DriftReport) -> None:
        """Ensure every unsatisfied required requirement has an OPEN card (idempotent)."""
        for req_id in self._gaps(report):
            if self._open_card_for(req_id) is not None:
                continue
            req = self._requirement(req_id)
            if req is None:
                continue
            self._add_card(req)

    def _add_card(self, req: Requirement) -> KanbanCard:
        verify = f'chimera drift "{self._spec_abs}" --only {req.id} -w "{self._ws}"'
        action = req.text or f"Make requirement '{req.id}' pass ({req.check}: {req.target})."
        card = self.board.add(
            title=f"[{req.id}] {(req.text or req.id)[:60]}",
            action=action,
            lane="solve",
            verify=verify,
        )
        card.metadata["requirement_id"] = req.id
        card.metadata["depends_on"] = list(req.depends_on)
        if req.risk:
            card.metadata["risk"] = req.risk
        self.board.save()
        return card

    def _deps_met(self, card: KanbanCard, satisfied: set[str]) -> bool:
        deps = card.metadata.get("depends_on") or []
        return all(dep in satisfied for dep in deps)

    def _reconcile_blocked(self, satisfied: set[str]) -> None:
        """Move backlog↔blocked by dependency readiness (visibility; picking uses the same rule)."""
        for card in self.board.cards("backlog"):
            if not self._deps_met(card, satisfied):
                self.board.move(card.id, "blocked")
        for card in self.board.cards("blocked"):
            if self._deps_met(card, satisfied):
                self.board.move(card.id, "backlog")

    def _next_ready_card(self, satisfied: set[str]) -> KanbanCard | None:
        for card in self.board.cards("backlog"):
            if self._deps_met(card, satisfied):
                return card
        return None

    def _is_high_risk(self, card: KanbanCard) -> bool:
        return str(card.metadata.get("risk", "")).lower() == "high"

    def _work(self, card: KanbanCard) -> None:
        self.board.move(card.id, "doing")
        try:
            result = self.solve_card.run(card)
        except Exception as exc:  # noqa: BLE001 — a lane failure parks the card for review
            _log.warning("solve lane raised on card %s: %s", card.id, exc)
            result = LaneResult(success=False, answer=f"error: {exc}")
        self.board.record_result(card.id, success=result.success, result=result.answer)
        self.board.move(card.id, "done" if result.success else "review")

    def _set(self, status: ProjectStatus, note: str) -> ProjectState:
        self.state.status = status
        self.state.note = note
        return self._save()

    def _save(self) -> ProjectState:
        self.state.save(self.state_path)
        return self.state
