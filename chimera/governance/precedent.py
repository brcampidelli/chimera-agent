"""Guarded precedent store — the kernel's case law (AgentTrust v2 precedent RAG).

The semantic judge is expensive. The kernel records each judge verdict here; a verdict
becomes a usable *precedent* only after it has been observed ``min_agreement`` times for
the same action (two judges agreeing), guarding against a single noisy call. Once
admitted, :meth:`recall` returns the precedent for a *similar* action (token overlap) —
RAG over case law — so the kernel decides cheaply without re-invoking the judge.
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

    def observe(self, action: str, decision: Decision) -> bool:
        """Record a judge verdict for ``action``. Returns True once it is confirmed."""
        existing = self._candidates.get(action)
        if existing is None or existing.decision != decision.value:
            existing = _Candidate(decision.value, 1, sorted(_tokens(action)))
        else:
            existing.agreements += 1
        self._candidates[action] = existing
        self._save()
        return existing.agreements >= self.min_agreement

    def recall(self, action: str) -> Decision | None:
        """Return a confirmed precedent's decision for a similar action (or None)."""
        query = _tokens(action)
        if not query:
            return None
        best: _Candidate | None = None
        best_score = 0.0
        for candidate in self._candidates.values():
            if candidate.agreements < self.min_agreement:
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
        for action, data in raw.items():
            self._candidates[action] = _Candidate(
                data["decision"], int(data["agreements"]), list(data.get("tokens", []))
            )

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            action: {"decision": c.decision, "agreements": c.agreements, "tokens": c.tokens}
            for action, c in self._candidates.items()
        }
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
