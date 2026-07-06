"""ACE — an incremental, delta-updated strategy playbook (defeats context collapse).

Rewriting a whole instruction/context every time a model reflects erases useful detail — the
"context collapse" ACE (Agentic Context Engineering) names. The fix is structural: keep a
**playbook** of small, reusable bullets and only ever apply *deltas* to it — add a new bullet,
reinforce one that helped, deprecate one that misled — never a monolithic rewrite. Bullets carry
helpful/harmful counters, so pruning is data-driven, and a periodic grow-and-refine pass dedupes
near-identical entries and caps the size.

The three ACE roles map cleanly onto what Chimera already has: the Generator is the agent loop, the
Reflector+Curator is the model call here that turns an outcome into deltas. The model seam is
injected, so the whole loop is testable without a network. The anti-collapse guarantee is a
property of the code, not the prompt: ``curate`` only ever *applies deltas* — it can never replace
the existing playbook.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("evolution.playbook")

DeltaOp = Literal["add", "reinforce", "deprecate"]
ItemStatus = Literal["active", "deprecated"]
_WS = re.compile(r"\s+")
_JSON = re.compile(r"\{.*\}", re.DOTALL)
_SLUG = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text.strip().lower())


def _slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-") or "general"


@dataclass
class PlaybookItem:
    id: str
    content: str
    section: str = "general"
    helpful: int = 0
    harmful: int = 0
    status: ItemStatus = "active"

    @property
    def score(self) -> int:
        return self.helpful - self.harmful

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "section": self.section,
            "helpful": self.helpful,
            "harmful": self.harmful,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlaybookItem:
        status: ItemStatus = "deprecated" if data.get("status") == "deprecated" else "active"
        return cls(
            id=str(data["id"]),
            content=str(data["content"]),
            section=str(data.get("section", "general")),
            helpful=int(data.get("helpful", 0)),
            harmful=int(data.get("harmful", 0)),
            status=status,
        )


@dataclass
class Delta:
    """One incremental edit: add a bullet, or reinforce/deprecate an existing one by id."""

    op: DeltaOp
    content: str = ""
    target: str = ""
    section: str = "general"


class Playbook:
    """A grow-and-refine collection of strategy bullets, edited only through deltas."""

    def __init__(
        self,
        items: list[PlaybookItem] | None = None,
        *,
        max_items: int = 50,
        deprecate_at: int = 2,
    ) -> None:
        self.items = list(items or [])
        self.max_items = max_items
        self.deprecate_at = deprecate_at  # a harmful-over-helpful margin this large auto-deprecates
        self._seq = self._max_seq()

    def _max_seq(self) -> int:
        highest = 0
        for item in self.items:
            tail = item.id.rsplit("-", 1)[-1]
            if tail.isdigit():
                highest = max(highest, int(tail))
        return highest

    def active(self) -> list[PlaybookItem]:
        return [i for i in self.items if i.status == "active"]

    def _find(self, item_id: str) -> PlaybookItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def _match(self, content: str) -> PlaybookItem | None:
        target = _norm(content)
        return next((i for i in self.active() if _norm(i.content) == target), None)

    def add(self, content: str, section: str = "general") -> PlaybookItem | None:
        """Add a new bullet — or, if an active one already says the same thing, reinforce it."""
        if not content.strip():
            return None
        existing = self._match(content)
        if existing is not None:  # dedupe: reinforce rather than duplicate (grow-and-refine)
            existing.helpful += 1
            return existing
        self._seq += 1
        item = PlaybookItem(id=f"{_slug(section)}-{self._seq}", content=content.strip(), section=section)
        self.items.append(item)
        return item

    def apply(self, delta: Delta) -> bool:
        if delta.op == "add":
            return self.add(delta.content, delta.section) is not None
        item = self._find(delta.target)
        if item is None:
            return False
        if delta.op == "reinforce":
            item.helpful += 1
        elif delta.op == "deprecate":
            item.harmful += 1
            if item.score <= -self.deprecate_at:  # consistently misleading — retire it
                item.status = "deprecated"
        return True

    def apply_all(self, deltas: list[Delta]) -> int:
        applied = sum(1 for delta in deltas if self.apply(delta))
        self.refine()
        return applied

    def refine(self) -> None:
        """Grow-and-refine: merge duplicate bullets, then cap size by deprecating the weakest."""
        seen: dict[str, PlaybookItem] = {}
        for item in self.active():
            key = _norm(item.content)
            if key in seen:  # merge counters into the first occurrence, deprecate the rest
                seen[key].helpful += item.helpful
                seen[key].harmful += item.harmful
                item.status = "deprecated"
            else:
                seen[key] = item
        active = [i for i in self.items if i.status == "active"]
        if len(active) > self.max_items:
            for item in sorted(active, key=lambda i: (i.score, i.id))[: len(active) - self.max_items]:
                item.status = "deprecated"

    def render(self, max_items: int = 20, *, with_ids: bool = False) -> str:
        """Render the top active bullets as an advisory block (with ids when curating)."""
        active = sorted(self.active(), key=lambda i: (-i.score, i.id))[:max_items]
        if not active:
            return ""
        lines = ["Playbook (learned strategies — advisory):"]
        for item in active:
            tag = f"{item.id} " if with_ids else ""
            lines.append(f"- {tag}[{item.section}] {item.content}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_items": self.max_items,
            "deprecate_at": self.deprecate_at,
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Playbook:
        items = [PlaybookItem.from_dict(d) for d in data.get("items", [])]
        return cls(
            items,
            max_items=int(data.get("max_items", 50)),
            deprecate_at=int(data.get("deprecate_at", 2)),
        )


_CURATE_SYSTEM = (
    "You maintain an incremental strategy PLAYBOOK for an agent. Given a task, its outcome, and the "
    "current playbook, propose SMALL incremental changes — never a rewrite. Reply with ONLY a JSON "
    'object {"deltas": [...]}, where each delta is one of: '
    '{"op":"add","content":"<concise reusable strategy or pitfall>","section":"strategy|pitfall|check"}, '
    '{"op":"reinforce","target":"<id of a bullet that helped>"}, or '
    '{"op":"deprecate","target":"<id of a bullet that misled>"}. '
    "Add at most a few bullets; each must be GENERAL and reusable across tasks, never task-specific "
    "constants. Prefer reinforcing an existing bullet over adding a near-duplicate."
)


def _parse_deltas(text: str) -> list[Delta]:
    match = _JSON.search(text)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    raw = data.get("deltas") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    deltas: list[Delta] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        op = entry.get("op")
        if op not in ("add", "reinforce", "deprecate"):
            continue
        deltas.append(
            Delta(
                op=op,
                content=str(entry.get("content", "")),
                target=str(entry.get("target", "")),
                section=str(entry.get("section", "general")),
            )
        )
    return deltas


class SupportsProposeDeltas(Protocol):
    """Proposes incremental playbook deltas from a task outcome."""

    def propose(self, task: str, outcome: str, playbook_text: str) -> list[Delta]: ...


class BackendDeltaProposer:
    """Default proposer: ask the model to reflect on an outcome and emit incremental deltas."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def propose(self, task: str, outcome: str, playbook_text: str) -> list[Delta]:
        user = f"Task:\n{task}\n\nOutcome:\n{outcome}\n\nCurrent playbook:\n{playbook_text or '(empty)'}"
        try:
            raw = self.backend.complete(
                [Message(role="system", content=_CURATE_SYSTEM), Message(role="user", content=user)],
                model=self.model,
                temperature=0.2,
            ).content
        except Exception as exc:  # noqa: BLE001 — a flaky curator must not fail the run
            _log.warning("delta proposer failed, no update: %s", exc)
            return []
        return _parse_deltas(raw)


class PlaybookCurator:
    """Turns a run's outcome into incremental playbook deltas — the ACE reflect+curate step."""

    def __init__(self, proposer: SupportsProposeDeltas) -> None:
        self.proposer = proposer

    def curate(self, playbook: Playbook, task: str, outcome: str) -> int:
        """Reflect on the outcome and apply the proposed deltas; return how many landed.

        Only ever *applies deltas* to the existing playbook — it structurally cannot replace it,
        which is exactly the anti-context-collapse guarantee.
        """
        deltas = self.proposer.propose(task, outcome, playbook.render(with_ids=True))
        applied = playbook.apply_all(deltas)
        _log.debug("curated playbook: %d/%d deltas applied", applied, len(deltas))
        return applied
