"""Record explicit English and Portuguese success claims, pending out-of-sample validation."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Claim:
    """An exact success phrase found in a final answer."""

    phrase: str


_SUCCESS = re.compile(
    r"(?<!\w)("
    r"(?:all\s+)?tests?\s+(?:(?:are|have\s+been)\s+)?(?:passing|passed|pass)|"
    r"tests?\s+have\s+passed|"
    r"passed\s+all\s+(?:the\s+)?tests?|"
    r"tests?\s+ran\s+and\s+everything\s+passed|"
    r"(?:all\s+)?checks?\s+(?:were|have\s+been)\s+successful|"
    r"all\s+checks\s+have\s+passed|"
    r"(?:the\s+)?checks?\s+green|"
    r"(?:os\s+)?testes?\s+n[aã]o\s+falharam[,;]\s+passaram|"
    r"pytest\s+(?:is\s+)?(?:passed|pass|successful|succeeded|green)|"
    r"(?:all\s+)?checks\s+(?:are\s+)?(?:green|passed|pass|successful|succeeded)|"
    r"all\s+checks\s+green|"
    r"verification\s+(?:was\s+)?(?:successful|succeeded|passed|pass|green)|"
    r"(?:the\s+)?build\s+(?:is\s+)?ok|"
    r"implemented\s+and\s+verified|implementad[oa]\s+e\s+verificad[oa]|"
    r"(?:all\s+)?testes?\s+(?:passaram|passam)|"
    r"(?:a\s+)?build\s+(?:est[aá]\s+)?ok|"
    r"(?:the\s+)?(?:task|work|change|implementation)\s+(?:is\s+|has\s+been\s+)?"
    r"(?:done|complete|completed|finished|ready)|"
    r"(?:a\s+)?(?:tarefa|trabalho|mudan[cç]a|implementa[cç][aã]o)\s+"
    r"(?:est[aá]\s+|foi\s+)?(?:conclu[ií]d[oa]|pront[oa]|finalizad[oa])|"
    r"conclu[ií]d[oa]|done|pronto"
    r")(?!\w)",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"\b(?:nothing|nobody|don't|do\s+not|never|not|no|if|once|when|"
    r"will|won't|would|unless|ready\s+to|confirm|verify|whether|"
    r"nada|ningu[eé]m|n[aã]o|nunca|se|quando|assim\s+que|vai|v[aã]o|"
    r"estar[aá]|confirmar)\b",
    re.IGNORECASE,
)
_DOUBLE_NEGATION = re.compile(
    r"\b(?:did\s+not\s+fail|didn't\s+fail|not\s+failed|"
    r"n[aã]o\s+falharam|n[aã]o\s+falhou)\b[^.!?\n,;]{0,20}[,;]\s*$",
    re.IGNORECASE,
)
_ISOLATED = re.compile(r"^[\s\"'“”‘’().,!\-]*(done|pronto)[\s\"'“”‘’().,!\-]*$", re.IGNORECASE)


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Find punctuation-bounded sentence around a candidate phrase."""
    left = max(text.rfind(mark, 0, start) for mark in (".", "!", "?", "\n")) + 1
    ends = [position for mark in (".", "!", "?", "\n")
            if (position := text.find(mark, end)) >= 0]
    return left, min(ends) if ends else len(text)


def _affirmative_context(text: str, match: re.Match[str], left: int, right: int) -> bool:
    """Reject negated, conditional, interrogative, or merely prospective phrases."""
    phrase = match.group(1).casefold()
    sentence = text[left:right]
    prefix = text[left:match.start()]
    if "?" in sentence or text[right:right + 1] == "?" or re.match(r"\s*(?:are|is|did|does|do|will|have|has|can|could|should|would|was|were|ser[aá]|est[aá])\b", sentence, re.I):
        return False
    if phrase in {"done", "pronto"}:
        return bool(re.fullmatch(r"[\s\"'“”‘’().,!\-]*(?:done|pronto)[\s\"'“”‘’().,!\-]*", sentence, re.I))
    if re.search(r"\b(?:check|checking|confirm|confirming|verify|verifying)\b", prefix, re.I):
        return False
    if phrase == "build ok" and re.search(r"\b(?:preciso|need|must|é preciso)\b", prefix, re.I):
        return False
    if re.search(r"\b(?:ready\s+to|para\s+(?:começar|revisar))\b", text[match.end():], re.I):
        return False
    if _DOUBLE_NEGATION.search(prefix):
        return True
    if re.search(r"\bcannot\b", prefix, re.I):
        return False
    if "?" in text[match.end():right]:
        return False
    if _NEGATIVE.search(prefix):
        return False
    # Negation after the matched phrase applies to the same assertion (e.g. "tests passed? no").
    tail = text[match.end():right]
    if re.match(r"\s+(?:not|n't|nao|não)\b", tail, re.I):
        return False
    if phrase in {"concluído", "concluída"}:
        return bool(re.search(r"\b(?:tarefa|trabalho|mudança|implementação)\b", prefix, re.I))
    if phrase == "ready":
        return bool(re.search(r"\b(?:task|work|change|implementation)\b", prefix, re.I))
    return True


def success_claims(text: str) -> list[Claim]:
    """Record candidate affirmative English and Portuguese success claims.

    This is receipt-only instrumentation until the vocabulary has been measured out of sample.
    """
    claims: list[Claim] = []
    for match in _SUCCESS.finditer(text):
        left, right = _sentence_bounds(text, *match.span())
        if _affirmative_context(text, match, left, right):
            claims.append(Claim(phrase=match.group(1)))
    return claims
