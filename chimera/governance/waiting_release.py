"""Turning on the owner's "Ignore permissions" switch also answers the cards already waiting.

The switch was read at each NEW question (`bypass_owner_for`, `LedgeredTool`, the kernel's REVIEW
path). A card that was already waiting when the owner turned it on kept waiting, and its turn stayed
parked on it: the owner had said "stop asking me" and was still being asked. This releases those
cards, one by one, under the same criteria the switch applies to a new question.

Three things decide it, and each one can only refuse:

- the call is judged again with :func:`chimera.governance.bypass.bypass_safe`, on its real arguments
  and the kernel's verdict. The arguments are read from the memory of the process that asked
  (:func:`chimera.governance.pending.waiting_call`), never from the question file;
- the exceptions the live switch already makes for cards: a block or warn verdict, an irreversible
  browser action, a Google search in the owner's Chrome, a question the person must type an answer to,
  an open pull request, and any card an approval rule (`approval_rules`) still asks about;
- a card is answered only through :func:`chimera.governance.pending.answer` with the code this process
  holds for it. A question asked by another process has no code here, so it is left waiting.

The conversation of each card is found from its turn (``origin_of``, the route's
``approval_origin``), never from an id the request carried. A card of another conversation is not
counted and not touched. A background work's card is counted as left: background work never gets the
switch (`turn_bypass_switch` requires ``background is None``), so nothing here releases it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.governance.pending import PendingApproval, answer, pending, waiting_call
from chimera.telemetry import get_logger

_log = get_logger("governance.waiting_release")

#: Tools whose card the live switch never releases (`bypass_owner_for`), however the call judges.
_NEVER_RELEASED_TOOLS = frozenset({"ask_user", "open_pull_request", "browser"})
#: Verdict levels the live switch never releases.
_NEVER_RELEASED_LEVELS = frozenset({"block", "warn"})
#: The action prefix of a Google search in the owner's Chrome (`always_ask`), never released.
_GOOGLE_SEARCH_PREFIX = "google search:"


@dataclass(frozen=True)
class WaitingRelease:
    """What the switch did to the cards of one conversation that were waiting when it was turned on."""

    approved_waiting: int
    """Cards answered yes by the switch, now."""

    left_waiting: int
    """Cards of the conversation still waiting: the owner must answer them, or they time out."""


def in_conversation(session_id: str) -> Callable[[dict[str, str]], bool]:
    """The cards of one conversation: the one the switch was turned on in."""
    return lambda origin: origin.get("session_id") == session_id


def in_project(
    project_key_of: Callable[[str], str], project_key: str
) -> Callable[[dict[str, str]], bool]:
    """The cards of every conversation whose folder belongs to one registered project.

    ``project_key_of`` maps a folder to the key of the project it belongs to (the registry's
    ``project_key_for``). An empty key matches nothing, so a folder with no project is never swept
    up by a project's switch.
    """
    def belongs(origin: dict[str, str]) -> bool:
        key = project_key_of(origin.get("workspace", "")) if origin.get("workspace") else ""
        return bool(key) and key == project_key

    return belongs


def switched_on(
    conversation_on: Callable[[str], bool],
    project_on: Callable[[str], bool],
    project_key_of: Callable[[str], str],
) -> Callable[[dict[str, str]], bool]:
    """The cards of every conversation the switches cover: its own switch, or its folder's project."""

    def belongs(origin: dict[str, str]) -> bool:
        session_id = origin.get("session_id", "")
        if session_id and conversation_on(session_id):
            return True
        workspace = origin.get("workspace", "")
        key = project_key_of(workspace) if workspace else ""
        return bool(key) and project_on(key)

    return belongs


def release_waiting_cards(
    home: Path,
    *,
    belongs: Callable[[dict[str, str]], bool],
    origin_of: Callable[[str], dict[str, str]],
    audit: Any = None,
    now: float | None = None,
) -> WaitingRelease:
    """Answer yes to every waiting card that ``belongs`` selects and the switch may release; count the rest.

    ``belongs`` is given the turn's origin (``approval_origin``: session, workspace, and the work's
    title for a background one) and decides which conversation or project the switch was turned on in.
    """
    approved = 0
    left = 0
    for card in pending(home, now=now):
        if not card.run_id:
            continue
        origin = origin_of(card.run_id)
        if not origin or not belongs(origin):
            continue
        call = waiting_call(card.id)
        if "work" in origin or call is None or not _releasable(home, card, call, origin):
            left += 1
            continue
        if not answer(home, card.id, True, via="app"):
            # No code here (the question was asked by another process), or its wait just ended.
            left += 1
            continue
        approved += 1
        if audit is not None:
            audit.record(
                "bypass_permissions",
                {
                    "action": card.action,
                    "reason": "bypass_permissions",
                    "tool": call.tool,
                    "run_id": card.run_id,
                    "released": "switch_turned_on",
                },
            )
        _log.info("switch on: released a waiting card (%s)", call.tool)
    return WaitingRelease(approved_waiting=approved, left_waiting=left)


def _releasable(
    home: Path, card: PendingApproval, call: Any, origin: dict[str, str]
) -> bool:
    """Whether the switch may answer this card yes now. Every check here can only refuse."""
    from chimera.governance.approval_rules import matching_rule, tool_and_action
    from chimera.governance.bypass import bypass_safe
    from chimera.governance.policy import Decision, RuleSet

    if card.kind or card.irreversible:
        return False
    if card.decision.strip().lower() in _NEVER_RELEASED_LEVELS:
        return False
    if card.action.startswith(_GOOGLE_SEARCH_PREFIX):
        return False
    if call.tool in _NEVER_RELEASED_TOOLS:
        return False
    tool, shown = tool_and_action(card.action)
    if tool in _NEVER_RELEASED_TOOLS:
        return False
    workspace = origin.get("workspace", "")
    if not workspace:
        # Without the folder an approval rule cannot be looked up, and a rule is an answer the
        # owner gave earlier that this must not override.
        return False
    if matching_rule(home, workspace, tool, shown) is not None:
        return False
    verdict = RuleSet().evaluate(shown)
    if verdict is not None and verdict.decision in {Decision.BLOCK, Decision.WARN}:
        return False
    # The same two checks the live paths make: the ledger on the call, the kernel on the call and
    # on the text the card showed (`governed_tool` and `profile._bypass_safe`).
    if not bypass_safe(call.tool, call.args, call.evaluation):
        return False
    return not call.shown or bypass_safe(call.tool, {"command": call.shown})
