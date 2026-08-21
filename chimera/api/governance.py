"""Governance / Security surface for the desktop app: the injection red-team scoreboard + the audit log.

Two pure, read/compute helpers, both honest by construction:

- :func:`run_injection_suite` runs the synthetic red-team corpus twice (with and WITHOUT the defenses)
  and returns a side-by-side comparison. It fabricates nothing: every number is derived from the two
  :class:`~chimera.eval.injection.RedTeamReport` runs, and the attacks that get through EVEN WHEN
  DEFENDED (``leaks_defended`` — expect ``http_exfil``, exfil through an allowed tool) are named, not
  hidden. The corpus is synthetic and needs no LLM key, so this is instant and side-effect free. It
  measures defense-in-depth of an already-injected agent, NOT the model's susceptibility to injection.
- :func:`read_audit` reads the append-only governance audit log (JSONL) newest-first. It is written
  by CLI guarded/tainted runs (``chimera run --guard`` / ``solve --guard/--taint``) and by the app's
  own tool stack when a defence actually fires. An empty log is still the expected state — but it
  now means "nothing has happened", not "nothing is recording", and those are opposite claims.

Both helpers take ``settings`` so they can report what is armed **in this install** rather than what
the code is capable of. A scoreboard that describes a build the reader does not have is the same
failure as a control that saves and does nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.config import Settings, get_settings
from chimera.eval.injection import default_attacks, run_redteam
from chimera.governance.audit import AuditLog


def run_injection_suite(settings: Settings | None = None) -> dict[str, Any]:
    """Run the red-team corpus with and without the defenses; return the honest comparison.

    Both runs use the SAME ordered corpus, so the two ``.outcomes`` lists join cleanly by ``.id``.
    ``defended_asr`` should sit well below ``undefended_asr``; ``leaks_defended`` names the attacks
    the defenses still miss (the honest gap), so the scoreboard can never over-claim.

    The suite measures ONE layer: the taint ledger's adaptive narrowing, which it exercises with
    ``narrow_on_taint=True``. That layer is armed on every app surface — but it is switchable, and
    with ``CHIMERA_TAINT_NARROW=0`` this same defended figure would describe a build the reader does
    not have. So ``armed`` reports the install rather than the capability, and ``trust_kernel``
    reports the layer these numbers do NOT cover: the BLOCK/REVIEW policy rules are exercised by
    nothing in this suite. It is a constant `False` because that is a fact about the SUITE, which
    does not change with configuration — unlike where the rules run, which since `assemble_registry`
    began calling `govern_step` is `chimera run --guard`, `solve --guard`, and the run and turn
    endpoints whenever ``CHIMERA_GOVERNANCE`` is set. Naming an unmeasured layer is cheaper than
    having someone infer it is scored from a good number.
    """
    settings = settings or get_settings()
    defended = run_redteam(default_attacks(), defended=True)
    undefended = run_redteam(default_attacks(), defended=False)
    dsum = defended.summary()
    usum = undefended.summary()

    # Per-category ASR (with/without) + the count of attacks in that category. The `asr_<cat>` keys
    # come straight from each run's summary; the count is folded from the defended run's outcomes
    # (the corpus is identical, so either run gives the same per-category counts).
    counts: dict[str, int] = {}
    for o in defended.outcomes:
        counts[o.category] = counts.get(o.category, 0) + 1
    by_category = [
        {
            "category": cat,
            "defended_asr": dsum.get(f"asr_{cat}", 0.0),
            "undefended_asr": usum.get(f"asr_{cat}", 0.0),
            "count": counts[cat],
        }
        for cat in sorted(counts)
    ]

    # Per-attack join: same ordered corpus, so index-align the two outcome lists by id.
    d_blocked = {o.id: o.blocked for o in defended.outcomes}
    u_blocked = {o.id: o.blocked for o in undefended.outcomes}
    attacks = [
        {
            "id": a.id,
            "category": a.category,
            "harmful_tool": a.harmful_tool,
            "blocked_defended": d_blocked.get(a.id, False),
            "blocked_undefended": u_blocked.get(a.id, False),
        }
        for a in default_attacks()
    ]

    return {
        "total_attacks": int(dsum.get("attacks", 0.0)),
        "defended_asr": dsum.get("attack_success_rate", 0.0),
        "undefended_asr": usum.get("attack_success_rate", 0.0),
        "defended_block_rate": dsum.get("block_rate", 0.0),
        "undefended_block_rate": usum.get("block_rate", 0.0),
        "by_category": by_category,
        "attacks": attacks,
        # The attacks that STILL get through with defenses on — the honest gap, named out loud.
        "leaks_defended": defended.leaks(),
        # What this scoreboard is about, and whether it is switched on where you are reading it.
        "defense": "taint_narrowing",
        "armed": bool(settings.taint_narrow),
        # MEASURED, not installed — and the distinction is why this is a constant rather than
        # something derived from `governance_mode`. It was briefly derived, and that was wrong
        # in the dangerous direction: the app hides this whole line when the flag is true
        # (Governance.tsx), so turning governance on would have silenced the disclaimer for
        # `/api/chat/stream`, `/v1/chat/completions`, `/api/kanban/run` and `/api/projects` —
        # four HTTP surfaces that do not go through `assemble_registry` and still have no
        # kernel. One boolean cannot describe a layer that is on some endpoints and not
        # others; what it CAN say truthfully is that nothing on this screen measures it.
        "trust_kernel": False,
    }


def read_audit(path: Path, *, limit: int = 200) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The audit log newest-first (capped at ``limit``), and the state of its hash chain.

    Returns ``([], ...)`` when the file is missing (``AuditLog.entries`` already handles that).
    Read-only — each entry is the arbitrary dict its writer persisted; the app.py handler flattens
    it for the UI.

    The chain is checked here because nothing checked it anywhere. Every entry carries the digest of
    the one before it, which is a cost paid on every write for a property — this log has not been
    edited — that no code and no screen ever asked about. An unverified chain is bookkeeping, not
    evidence: it detects tampering only if somebody walks it.

    Verified from the entries already read rather than by re-reading: this file grows for the life
    of the install, and reading it twice to answer one question is a cost that arrives later.
    """
    log = AuditLog(Path(path))
    entries = log.entries()
    check = log.verify(entries)
    chain = {
        "ok": check.ok,
        "checked": check.checked,
        "unchained": check.unchained,
        "broken_at": check.broken_at,
        "reason": check.reason,
    }
    return list(reversed(entries))[:limit], chain
