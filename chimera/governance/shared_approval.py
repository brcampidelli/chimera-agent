"""One decision, for every worker that asks it — and one prompt at a time.

Crew workers collaborate on a single task and merge into one workspace, which is why they already
share a :class:`~chimera.governance.ledger.SharedTaint`: content one worker fetched can flow to
another, so a fetch in any of them arms the narrowing in all of them.

The approval half was not shared. Each worker carried its own approver, so once the run was tainted
and a dangerous tool needed a decision, N workers asked N times — and they asked *concurrently*,
onto one terminal, where two prompts interleave into a question nobody can answer correctly. The
person is being asked about ONE run; they should be asked once.

Two things happen here and only the first is obvious:

**One prompt at a time.** A lock around the ask, so output never interleaves. This alone fixes the
unreadable case, and it does it whether or not the decisions are reused.

**One answer, reused.** The verdict is cached by the action as it was DESCRIBED — which is exactly
the sentence the person read. A worker asking about a different action gets a different key and a
fresh question. Reusing a *refusal* is uncontroversial; reusing an *approval* is the deliberate
part, and the argument is that the alternative is worse: a person who has just approved
`run_shell: npm test` for worker A, asked again for the identical action by worker B a second
later, is being asked to confirm something they cannot distinguish from what they already answered.
That is how a prompt stops being read.

The cache is per RUN, because that is what a `SharedApprovals` instance is. Nothing here persists.
"""

from __future__ import annotations

import threading
from typing import Any

from chimera.governance.approval import ApprovalLedger, Approver
from chimera.telemetry import get_logger

_log = get_logger("governance.shared_approval")


def _key(*args: Any) -> str:
    """The identity of a decision: the action as described, plus the reason it was questioned.

    Both, not just the action. The same command can be questioned for different reasons — a policy
    rule on one call and taint narrowing on the next — and an approval given for one is not an
    answer to the other.
    """
    if len(args) == 2:
        verdict, action = args
        return f"{getattr(verdict, 'reason', '') or ''}\x00{action}"
    assessment = args[0] if args else None
    return f"{getattr(assessment, 'reason', '') or ''}\x00"


class SharedApprovals:
    """One run's approval decisions, shared by every worker in it.

    Wrap once, hand the result to each worker. The ledger is shared too, so `blocked` answers for
    the run rather than for whichever worker happened to be asked.
    """

    def __init__(self, approver: Approver, *, ledger: ApprovalLedger | None = None) -> None:
        self._approver = approver
        #: Shared, so "was this run allowed to do its work" is one question with one answer.
        self.ledger = ledger if ledger is not None else ApprovalLedger()
        self._lock = threading.Lock()
        self._decided: dict[str, bool] = {}

    def approver(self) -> Approver:
        """The approver every worker in this run should be given."""

        def approve(*args: Any) -> bool:
            chave = _key(*args)
            with self._lock:
                # Inside the lock, not before it. Checking the cache outside would let two workers
                # both miss, both prompt, and produce the interleaved output this exists to stop.
                if chave in self._decided:
                    decidido = self._decided[chave]
                    _log.debug("reusing this run's answer (%s): %s", decidido, chave.split("\x00")[-1][:80])
                    return decidido
                decidido = bool(self._approver(*args))
                self._decided[chave] = decidido
                return decidido

        return approve

    @property
    def asked(self) -> int:
        """How many distinct decisions this run actually put to a person.

        Reported rather than inferred: "twelve dangerous calls, three questions" is the sentence
        that says whether the sharing worked, and counting prompts is the only way to know.
        """
        return len(self._decided)
