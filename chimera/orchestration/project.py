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
    # The config rails are persisted so a resume (`project run/step/approve`) rebuilds the SAME
    # config from disk. Without this, a resume reconstructs ProjectConfig with the default
    # max_iterations=20 — silently defeating a `--max-iterations N` the user set at start.
    max_iterations: int = 20
    require_plan_approval: bool = True

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write (mirrors the board): a crash mid-write must not truncate project.json and
        # brick every later `project status`/`run`/`resume` for this project.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

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
        # No explicit config on a resume → rebuild the rails from the DURABLE state (not defaults).
        # When a config IS given (start), it wins and is written back so the two stay in sync.
        self.config = config or ProjectConfig(
            max_iterations=state.max_iterations,
            require_plan_approval=state.require_plan_approval,
        )
        state.max_iterations = self.config.max_iterations
        state.require_plan_approval = self.config.require_plan_approval
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
        # Refuse a spec with no requirements. `check_drift` starts `aligned=True` and only a failing
        # REQUIRED requirement flips it false, so an empty (or all-optional) spec — e.g. a YAML typo
        # like `requirement:` for `requirements:` that parses to [] — would vacuously report "done"
        # having verified nothing. The spec is the only authority of done; an empty one is a mistake.
        if not any(r.required for r in spec.requirements):
            raise ValueError(
                "spec has no required requirements — nothing to verify; refusing to start "
                "(check the spec's `requirements:` block parsed correctly)"
            )
        cfg = config or ProjectConfig()
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
            max_iterations=cfg.max_iterations,
            require_plan_approval=cfg.require_plan_approval,
        )
        state.save(pdir / "project.json")
        return cls(
            spec, board, state,
            solve_card=solve_card, state_path=pdir / "project.json",
            check_drift_fn=check_drift_fn, config=cfg,
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
        # Only resume when the pause was the PLAN gate — not when a high-risk card is still pending
        # (that needs approve_card). Guarding this avoids writing an inconsistent running-but-pending
        # state to disk that a concurrent `project status` reader could observe.
        if self.state.status == "awaiting_approval" and self.state.pending_card_id is None:
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
        """Reject the pending high-risk card: park it in review and escalate to a human.

        Guarded (mirrors ``approve_card``): only the card that is actually awaiting approval can be
        denied. A wrong/stale ``card_id`` (a typo, or one re-submitted after the pause resolved) is a
        no-op — it must not forcibly escalate a healthy project or lose track of the real pending card.
        """
        if self.state.pending_card_id != card_id:
            return self.state
        # Guard the move (mirrors approve_card): the pending id lives in project.json, not the board,
        # so a card dropped from the board (a failed model_validate on load, or a hand-edit) would make
        # an unguarded board.move raise KeyError out to the CLI. A missing card just escalates cleanly.
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
        # A card stuck in `doing` is debris from a crash mid-_work (which is otherwise synchronous:
        # move→run→record→move all in one step). Return it to backlog so its requirement is retried
        # instead of being orphaned forever (a `doing` card is OPEN, so _sync_cards won't replace it,
        # yet _next_ready_card only scans `backlog` → permanent "no ready card" escalation).
        self._recover_orphaned_doing()
        report = self.check_drift(self.spec, self._ws)
        if report.aligned:
            return self._set("done", "spec aligned — project complete")
        self._sync_cards(report)
        satisfied = {r.id for r in report.results if r.satisfied}
        self._reconcile_blocked(satisfied)
        if self.config.require_plan_approval and not self.state.plan_approved:
            return self._set("awaiting_approval", "approve the initial plan to begin")
        # Past the plan gate — persist that the plan is approved, so a resumed run (which recomputes
        # ``require_plan_approval`` from the durable ``plan_approved``) never re-asks. Without this,
        # a ``start --yes`` (require_plan_approval=False, but plan_approved never set) would re-pause
        # for plan approval on the next resume — e.g. right after approving a high-risk card.
        if not self.state.plan_approved:
            self.state.plan_approved = True
            self._save()
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

    def _recover_orphaned_doing(self) -> None:
        """Return any card left in ``doing`` (crash debris) to ``backlog`` so it can be retried."""
        for card in self.board.cards("doing"):
            self.board.move(card.id, "backlog")

    def _gaps(self, report: DriftReport) -> list[str]:
        """Ids of unsatisfied requirements that need a card: every unsatisfied REQUIRED requirement,
        plus any unsatisfied requirement it (transitively) ``depends_on`` — a dependency must be
        worked too, or a required requirement blocked on it can never become ready."""
        satisfied = {r.id for r in report.results if r.satisfied}
        required = {r.id for r in self.spec.requirements if r.required}
        needed = {r.id for r in report.results if not r.satisfied and r.id in required}
        frontier = list(needed)
        while frontier:
            req = self._requirement(frontier.pop())
            if req is None:
                continue
            for dep in req.depends_on:
                if dep not in satisfied and dep not in needed:
                    needed.add(dep)
                    frontier.append(dep)
        return [r.id for r in report.results if not r.satisfied and r.id in needed]

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
