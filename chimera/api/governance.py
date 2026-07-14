"""Governance / Security surface for the desktop app: the injection red-team scoreboard + the audit log.

Two pure, read/compute helpers, both honest by construction:

- :func:`run_injection_suite` runs the synthetic red-team corpus twice (with and WITHOUT the defenses)
  and returns a side-by-side comparison. It fabricates nothing: every number is derived from the two
  :class:`~chimera.eval.injection.RedTeamReport` runs, and the attacks that get through EVEN WHEN
  DEFENDED (``leaks_defended`` — expect ``http_exfil``, exfil through an allowed tool) are named, not
  hidden. The corpus is synthetic and needs no LLM key, so this is instant and side-effect free. It
  measures defense-in-depth of an already-injected agent, NOT the model's susceptibility to injection.
- :func:`read_audit` reads the append-only governance audit log (JSONL) newest-first. It is written
  only by CLI guarded/tainted runs (``chimera run --guard`` / ``solve --guard/--taint``); the desktop
  chat does not write it, so an empty log is the honest, expected state — never a fabricated event.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.eval.injection import default_attacks, run_redteam
from chimera.governance.audit import AuditLog


def run_injection_suite() -> dict[str, Any]:
    """Run the red-team corpus with and without the defenses; return the honest comparison.

    Both runs use the SAME ordered corpus, so the two ``.outcomes`` lists join cleanly by ``.id``.
    ``defended_asr`` should sit well below ``undefended_asr``; ``leaks_defended`` names the attacks
    the defenses still miss (the honest gap), so the scoreboard can never over-claim.
    """
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
    }


def read_audit(path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    """Read the governance audit log newest-first (highest ``seq`` first), capped at ``limit``.

    Returns ``[]`` when the file is missing (``AuditLog.entries`` already handles that). Read-only —
    each entry is the arbitrary dict the CLI persisted; the app.py handler flattens it for the UI.
    """
    entries = AuditLog(Path(path)).entries()
    return list(reversed(entries))[:limit]
