"""Four confirmation classes, each mapped onto the governance that already enforces it (study 25, S11).

The plan (`bench/PLAN-study25-system-prompts.md` §7 S11) asks for one taxonomy of confirmations,
mapped to the owner's mandate, and says the confirmations are governance gates rather than sentences.
So this module adds **no gate and no approver**. It names, for each class, the vocabulary the tree
already speaks — a :class:`~chimera.governance.policy.Decision`, who can release it, which layer
holds it and under which settings — and `tests/test_the_confirmation_classes_are_governance_gates.py`
drives each class's browser example through that layer and checks the row says what the layer does.

**The classes, by who has to act:**

- **hand over** — the person does it themselves: a sign-in, a two-step code, a captcha, a payment,
  typing a password. No approver can release it to the agent, the way no approver can release a
  BLOCK: the browser situation hands the page over and the loop ends the run.
- **confirm at action time** — the agent may do it, but only after a person says yes to *this* call.
  A REVIEW asked when the action is about to run; a yes given earlier, in the request, does not count.
- **pre-approved by the request** — a REVIEW the person's own request already answers, because it
  named the target. The taint ledger's ``authority`` mode is exactly this: a page the request named
  does not arm the narrowing.
- **none** — reading, looking and working inside the agent's own space. ALLOW.

**Where the owner's mandate sits.** The mandate in `pending.py`'s docstring — "confirm before
billing, before a destructive migration, before touching RLS" — is the second class. The examples
below are written generically, because the deployment's own list lives in its private operations
repository, not in this one.

**What this mapping cannot promise.** Every gate here but the first exists only where its layer is
installed. With ``CHIMERA_GOVERNANCE=off`` (the default) and outside the API server, nothing asks
anybody; ``requires`` says so row by row rather than letting a table read as protection it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from chimera.governance.policy import Decision


class ConfirmationClass(StrEnum):
    HAND_OVER = "hand_over"
    CONFIRM_AT_ACTION = "confirm_at_action"
    PRE_APPROVED_BY_REQUEST = "pre_approved_by_request"
    NONE = "none"


@dataclass(frozen=True)
class Confirmation:
    """One class, in the governance vocabulary that enforces it."""

    cls: ConfirmationClass
    #: The kernel's word for what happens to the agent's own attempt.
    decision: Decision
    #: Who can let the agent go ahead: ``nobody``, ``a person, at the call``, ``the request`` or ``-``.
    released_by: str
    #: The code that holds the gate, as ``module:attribute`` pointers the test resolves.
    enforced_by: tuple[str, ...]
    #: The settings under which that code is in the path at all.
    requires: str
    #: Browser situations in this class.
    browser: tuple[str, ...]
    #: Mandate items in this class, in general words.
    mandate: tuple[str, ...]


CONFIRMATIONS: tuple[Confirmation, ...] = (
    Confirmation(
        ConfirmationClass.HAND_OVER,
        Decision.BLOCK,
        "nobody",
        (
            "chimera.tools.browser_situation:BrowserSituation",
            "chimera.core.agent:_pending_handover",
        ),
        "CHIMERA_BROWSER_SITUATION=1 (off by default); holds in every governance mode",
        (
            "a sign-in page or a password field",
            "a two-step verification code",
            "a captcha or a bot-check page",
            "a card or payment field",
            "typing into a password, one-time code or card field",
        ),
        ("entering the owner's credentials", "approving a sign-in on the owner's device",
         "paying with the owner's card"),
    ),
    Confirmation(
        ConfirmationClass.CONFIRM_AT_ACTION,
        Decision.REVIEW,
        "a person, at the call",
        (
            "chimera.governance.ledger_tool:DANGEROUS_WHEN_TAINTED",
            "chimera.governance.ledger_tool:LedgeredTool",
        ),
        "taint narrowing on (the API server by default, CHIMERA_TAINT_NARROW; elsewhere "
        "CHIMERA_GOVERNANCE=observe|enforce) with CHIMERA_TAINT_AUTHORITY=provenance; the Code "
        "screen asks with a card, an unattended surface refuses",
        (
            "clicking, typing or navigating once the run has read a page",
            "submitting a form that sends, buys, publishes or deletes",
            "typing personal details the request did not give",
        ),
        ("changes to billing or payments", "a destructive database migration",
         "access policy or row-level security in production", "rotating a secret",
         "a financial action outside its configured limit", "a deploy that can break production"),
    ),
    Confirmation(
        ConfirmationClass.PRE_APPROVED_BY_REQUEST,
        Decision.REVIEW,
        "the request",
        (
            "chimera.governance.ledger:TaintLedger.set_instruction",
            "chimera.governance.ledger:TaintLedger.run_tainted",
        ),
        "the same narrowing with CHIMERA_TAINT_AUTHORITY=authority (off by default) on a surface "
        "that tells the ledger the request",
        (
            "acting on the page the request named, with the values it gave",
        ),
        ("an action the owner's own message names, with its target",),
    ),
    Confirmation(
        ConfirmationClass.NONE,
        Decision.ALLOW,
        "-",
        ("chimera.governance.ledger_tool:browser_reads_loaded_page",),
        "always",
        (
            "reading the page already loaded: the element list, its text, a search in it",
            "opening a public page before the run has read anything",
        ),
        ("reading and monitoring", "live data queries", "work inside the agent's own workspace"),
    ),
)


def confirmation(cls: ConfirmationClass) -> Confirmation:
    """The row for ``cls``."""
    return next(row for row in CONFIRMATIONS if row.cls is cls)
