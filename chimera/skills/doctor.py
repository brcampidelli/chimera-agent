"""``/skill-doctor``: what the skills a run is told about cost in context, and which of them are unused.

Deterministic on purpose: nothing here calls a model. It reads the same two sources a run reads.

* **Installed bundles** that are switched on (and pass the project's pack and plugin scope): each one
  is one line in every prompt, so its cost is that line measured with the app's own estimator.
* **Learned cards**, only when the owner turned card reading on: a card is retrieved per task, so it
  is listed with what its block costs when injected, but kept out of the per-turn total. The block is
  rendered with `cards_context_block`, the function the prompt uses, at the card's own line budget.

Usage. A bundle's use is not recorded anywhere (history stores tool names per turn, not the skill
read), so a bundle is always "no record". A card is credited by a run that was told about it
(`CardRetriever.record_outcome`). A card with NO counters has no record, which is not zero; only a
card with a counter of zero, read while card reading is on and not on probation, is suggested for
switching off. A card on probation (``provisional``) is still being measured: the lifecycle policy
decides, so it is never suggested for switching off here.

The counters have no time window (they are totals), so ``usage_days`` is always ``None``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from chimera.core.context_budget import estimate_tokens
from chimera.evolution.card_retrieval import cards_context_block
from chimera.skills.bundles import _context_line, active, prompt_block

#: How a skill reached the folder. Codes, not words: the API carries them, the screen and the CLI
#: translate them. ``learned`` is a card the agent wrote from its own runs.
ORIGIN_PROJECT = "project"
ORIGIN_USER = "user"
ORIGIN_PLUGIN = "plugin"
ORIGIN_CATALOG = "catalog"
ORIGIN_LEARNED = "learned"

#: What the report suggests. Only ``disable`` is an action, and it is a suggestion: switching a skill
#: off is the owner's click on the Skills screen.
SUGGEST_DISABLE = "disable"
SUGGEST_KEEP = "keep"
SUGGEST_NO_RECORD = "no_record"
SUGGEST_READING_OFF = "reading_off"
SUGGEST_PROBATION = "probation"
SUGGEST_SINCE_PROMOTION = "since_promotion"

KIND_BUNDLE = "bundle"
KIND_CARD = "card"


@dataclass(frozen=True)
class SkillCost:
    name: str
    kind: str
    origin: str
    #: Estimated tokens this skill takes in the context it is carried in.
    tokens: int
    #: Runs the skill was injected into and credited, or ``None`` when nothing recorded it.
    injected_runs: int | None
    suggestion: str
    #: True when every turn carries the skill (a bundle line); False when only tasks that retrieve it.
    every_turn: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DoctorReport:
    #: Sorted by cost, largest first; ties by name, so the same folder always reads the same way.
    skills: tuple[SkillCost, ...]
    #: Tokens the bundle block adds to EVERY turn: the prompt's own heading and lines, measured.
    tokens_per_turn: int
    #: What the ``cards_k`` largest retrievable cards add to one task, measured on the block they make.
    cards_tokens_per_task_max: int
    cards_read: bool
    cards_k: int
    #: Usage counters are totals, not a window of days. Stated as ``None`` rather than a number of days.
    usage_days: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["skills"] = [skill.to_dict() for skill in self.skills]
        return out


def _estimate(text: str) -> int:
    """The app's own estimator, not a second one: a figure here must match the meter beside the box."""
    return estimate_tokens([{"role": "user", "content": text}])


def _is_under(source: str, folder: Path) -> bool:
    """Whether ``source`` lies inside ``folder``, both resolved first so links and ``..`` cannot fool it."""
    try:
        return Path(source).expanduser().resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def _bundle_origin(origin: str, source: str, workspace: Path | None) -> str:
    if origin == "plugin":
        return ORIGIN_PLUGIN
    if origin == "catalog":
        return ORIGIN_CATALOG
    if origin == "discovered":
        # Found in the folder the report is about (a path under it, not a string prefix), or in the
        # owner's own home.
        if workspace is not None and source and _is_under(source, workspace):
            return ORIGIN_PROJECT
        return ORIGIN_USER
    # An upload is handed over by the owner, whose own folder it is not.
    return ORIGIN_USER


def _bundle_costs(
    home: Path,
    *,
    only: Collection[str] | None,
    plugin_scope: Collection[str] | None,
    workspace: Path | None,
) -> list[SkillCost]:
    rows: list[SkillCost] = []
    for bundle in active(home):
        if only is not None and bundle.name not in only:
            continue
        # The same rule `context_lines` applies: on a surface with a plugin scope, only the listed
        # plugins reach the prompt, so they are the only ones a turn pays for.
        if (bundle.origin == "plugin" and plugin_scope is not None
                and bundle.source.removeprefix("plugin:") not in plugin_scope):
            continue
        rows.append(
            SkillCost(
                name=bundle.name,
                kind=KIND_BUNDLE,
                origin=_bundle_origin(bundle.origin, bundle.source, workspace),
                tokens=_estimate(_context_line(bundle)),
                injected_runs=None,
                suggestion=SUGGEST_NO_RECORD,
                every_turn=True,
            )
        )
    return rows


def _card_suggestion(card: Any, runs: int | None, *, cards_read: bool, promoted: bool) -> str:
    if not cards_read:
        return SUGGEST_READING_OFF
    if getattr(card, "status", "active") == "provisional":
        return SUGGEST_PROBATION
    if promoted and not runs:
        # Counted from the promotion, and nothing has credited it since: not idle, just not yet measured.
        return SUGGEST_SINCE_PROMOTION
    if runs is None:
        return SUGGEST_NO_RECORD
    return SUGGEST_DISABLE if runs == 0 else SUGGEST_KEEP


def _card_costs(
    cards: Iterable[Any],
    runs: dict[str, int],
    *,
    cards_read: bool,
    max_lines: int,
    promoted: Collection[str],
) -> list[SkillCost]:
    """``cards`` are `LearnedSkill` objects a run could retrieve; ``runs`` only the ones ever credited.

    A card's cost is its body, as the prompt renders it: the heading and the instruction are paid once
    per task, in the task total, not once per card.
    """
    rows: list[SkillCost] = []
    for card in cards:
        count = runs.get(card.name)
        rows.append(
            SkillCost(
                name=card.name,
                kind=KIND_CARD,
                origin=ORIGIN_LEARNED,
                tokens=_estimate(f"[{card.name}]\n{card.card_text(max_lines=max_lines)}"),
                injected_runs=count,
                suggestion=_card_suggestion(
                    card, count, cards_read=cards_read, promoted=card.name in promoted
                ),
                every_turn=False,
            )
        )
    return rows


def build_report(
    home: Path,
    *,
    only: Collection[str] | None = None,
    plugin_scope: Collection[str] | None = None,
    workspace: Path | None = None,
    cards: Iterable[Any] = (),
    runs: dict[str, int] | None = None,
    promoted: Collection[str] = (),
    cards_read: bool = False,
    cards_k: int = 0,
    max_lines: int = 4,
) -> DoctorReport:
    """The report for one folder. Pure over its inputs: the caller reads the disk and the card store."""
    card_list = list(cards)
    bundles = _bundle_costs(home, only=only, plugin_scope=plugin_scope, workspace=workspace)
    card_rows = _card_costs(
        card_list, runs or {}, cards_read=cards_read, max_lines=max_lines, promoted=promoted
    )
    skills = tuple(sorted(bundles + card_rows, key=lambda s: (-s.tokens, s.name)))
    # What a task really adds: the block the prompt would carry for the k largest cards, measured once.
    largest = sorted(card_list, key=lambda c: -_estimate(cards_context_block([c], max_lines_per_card=max_lines)))
    cards_max = (
        _estimate(cards_context_block(largest[:cards_k], max_lines_per_card=max_lines))
        if cards_read and cards_k > 0 and largest
        else 0
    )
    return DoctorReport(
        skills=skills,
        tokens_per_turn=_estimate(prompt_block(home, only=only, plugin_scope=plugin_scope)),
        cards_tokens_per_task_max=cards_max,
        cards_read=cards_read,
        cards_k=cards_k if cards_read else 0,
    )


def report_for(settings: Any, folder: Path | None) -> DoctorReport:
    """The report a run started in ``folder`` would be given, read from the app's own settings.

    ``folder`` must already be a resolved, existing directory (the route and the CLI check it). It
    narrows the bundles by the project's accepted pack, exactly as a run there is narrowed. ``None``
    means the whole home. Plugin scope is the attended one (none), as on the Skills screen.
    """
    from chimera.core.project_pack import applied_pack, bundle_filter
    from chimera.evolution import SkillStore

    home = Path(settings.home)
    only = bundle_filter(applied_pack(settings, folder))
    store = SkillStore(home / "skills.json")
    cards_read = bool(getattr(settings, "skill_cards", False))
    cards_k = int(getattr(settings, "skill_cards_k", 0) or 0)
    max_lines = int(getattr(settings, "skill_cards_max_lines", 4) or 4)
    return build_report(
        home,
        only=only,
        workspace=folder,
        cards=store.retrievable(),
        runs=store.credited_runs(),
        promoted=store.counted_since_promotion(),
        cards_read=cards_read,
        cards_k=cards_k,
        max_lines=max_lines,
    )


__all__ = [
    "DoctorReport",
    "SkillCost",
    "build_report",
    "report_for",
]
