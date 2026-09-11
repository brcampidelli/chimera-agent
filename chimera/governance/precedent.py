"""Guarded precedent store — the kernel's case law (AgentTrust v2 precedent RAG).

The semantic judge is expensive. The kernel records each judge verdict here; a verdict
becomes a usable *precedent* only after it has been observed ``min_agreement`` times for
the same action (two judges agreeing), guarding against a single noisy call. Once
admitted, :meth:`recall` returns the precedent for a *similar* action (token overlap) —
RAG over case law — so the kernel decides cheaply without re-invoking the judge.

Case law is keyed on the action **and its lineage**. A verdict the judge gave while the run was
clean is not a verdict about the same command after the run has consumed untrusted content: the
string is identical, the authority behind it is not (arXiv 2609.08472, read in the 2026-09-11
sweep — evidence that names lineage went 0/32 → 32/32 in another substrate, and `bench/
right_hand_governance` measured the question side of the same defect in #425). Two partitions,
``""`` and ``"tainted"``, not the context: the context would fragment the cache so finely nothing
matched twice, which is what `kernel.py` says about it; a two-valued authority bit does not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from chimera.governance.policy import Decision

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) >= 2}


@dataclass
class _Candidate:
    decision: str
    agreements: int
    tokens: list[str]
    lineage: str = ""
    action: str = ""


def _key(action: str, lineage: str) -> str:
    """One record per (lineage, action). ``\x1f`` (unit separator) cannot occur in a rendered action."""
    return f"{lineage}\x1f{action}" if lineage else action


class PrecedentStore:
    """Accumulates judge verdicts; admits a precedent after enough agreements."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        min_agreement: int = 2,
        min_overlap: float = 0.5,
    ) -> None:
        self.path = Path(path) if path else None
        self.min_agreement = min_agreement
        self.min_overlap = min_overlap
        self._candidates: dict[str, _Candidate] = {}
        self._load()

    def observe(self, action: str, decision: Decision, *, lineage: str = "") -> bool:
        """Record a judge verdict for ``action`` under ``lineage``. Returns True once it is confirmed.

        ``lineage`` is the authority the verdict was given under — ``""`` for a clean run,
        ``"tainted"`` once the run has consumed untrusted content. Agreements never cross it: two
        clean verdicts confirm a clean precedent and say nothing about the tainted one.
        """
        key = _key(action, lineage)
        existing = self._candidates.get(key)
        if existing is None or existing.decision != decision.value:
            existing = _Candidate(decision.value, 1, sorted(_tokens(action)), lineage, action)
        else:
            existing.agreements += 1
        self._candidates[key] = existing
        self._save()
        return existing.agreements >= self.min_agreement

    def recall(self, action: str, *, lineage: str = "") -> Decision | None:
        """Return a confirmed precedent's decision for a similar action under ``lineage`` (or None).

        A precedent from another lineage is never returned, however similar the action: that is
        the whole reason the key carries it.
        """
        query = _tokens(action)
        if not query:
            return None
        best: _Candidate | None = None
        best_score = 0.0
        for candidate in self._candidates.values():
            if candidate.agreements < self.min_agreement or candidate.lineage != lineage:
                continue
            tokens = set(candidate.tokens)
            overlap = len(query & tokens) / max(1, len(query | tokens))  # Jaccard
            if overlap >= self.min_overlap and overlap > best_score:
                best_score, best = overlap, candidate
        return Decision(best.decision) if best is not None else None

    def confirmed(self) -> int:
        return sum(1 for c in self._candidates.values() if c.agreements >= self.min_agreement)

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            return
        for key, data in raw.items():
            # A file written before lineage existed has no `lineage` field: every record in it
            # was learned with no ledger asked, which is the clean partition.
            lineage = str(data.get("lineage", ""))
            action = str(data.get("action", key))
            self._candidates[_key(action, lineage)] = _Candidate(
                data["decision"], int(data["agreements"]), list(data.get("tokens", [])),
                lineage, action,
            )

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            key: {
                "decision": c.decision, "agreements": c.agreements, "tokens": c.tokens,
                "lineage": c.lineage, "action": c.action,
            }
            for key, c in self._candidates.items()
        }
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
