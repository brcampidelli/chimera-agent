"""Memory nudges — suggest saving preferences the user states but hasn't stored.

Low-friction personalization: when a recent message expresses a first-person preference
("I prefer async", "I always use ruff"), and nothing like it is in memory yet, surface a
gentle suggestion to save it as a persona fact. It's a pure, deterministic function — the
caller decides whether/how to show the suggestion, and it never stores anything itself.

User messages are first-person, so this detects "I <verb> ..." directly rather than reusing
the graph's third-person extractor ("Bruno prefers ..."). "Already known" is a token-overlap
check, so "I use ruff" won't re-nudge when "Bruno uses ruff" is already stored.
"""

from __future__ import annotations

import re

from chimera.memory.tokens import fold_for_match

#: First-person preference statements, in the languages this app ships in.
#:
#: This was one English pattern — ``\bi (?:always )?(?:prefer|like|...)s?\b`` — and measured, that
#: is exactly what it detected. "eu prefiro respostas curtas", "prefiero respuestas cortas",
#: "je prefere des reponses courtes", "ich bevorzuge kurze antworten" and
#: "я предпочитаю краткие ответы" all returned NOTHING. Not fewer suggestions: none, ever. The
#: feature existed for one of ten audiences.
#:
#: Note what this fixed and what it did not. The tokenizer in this module was ASCII-only too, and
#: repairing that alone would have changed nothing measurable — the pattern rejects a sentence
#: before a token is ever taken. A fix that fixes nothing is worth naming rather than shipping.
#:
#: Romance and Germanic languages drop the pronoun freely ("prefiro", "prefiero"), so the pronoun is
#: optional there; requiring it would miss the commonest phrasing. English keeps it required,
#: because "like" without "I" is a simile far more often than a preference.
_PREFERENCE = re.compile(
    "|".join(
        (
            # English — pronoun required: bare "like" is usually a comparison, not a preference.
            r"\bi (?:always |usually |generally |really |only |never )?"
            r"(?:prefer|like|love|use|need|want|require|avoid|dislike|hate)s?\b",
            # Portuguese
            r"\b(?:eu )?(?:sempre |normalmente |realmente |so |nunca )?"
            r"(?:prefiro|gosto de|adoro|uso|preciso de|quero|evito|detesto|odeio)\b",
            # Spanish
            r"\b(?:yo )?(?:siempre |normalmente |realmente |solo |nunca )?"
            r"(?:prefiero|me gusta|me gustan|adoro|uso|necesito|quiero|evito|odio)\b",
            # French — "je" REQUIRED. French does not drop the subject pronoun, and with it
            # optional the folded "prefere" also matched Portuguese THIRD person ("o cliente
            # prefere respostas curtas") and Portuguese questions ("voce prefere qual?").
            # Both measured, both suggested saving somebody else's preference as the user's.
            r"\bje (?:toujours |normalement |vraiment |seulement |jamais )?"
            r"(?:prefere|aime|adore|utilise|ai besoin de|veux|evite|deteste)\b",
            # Italian
            r"\b(?:io )?(?:sempre |di solito |davvero |solo |mai )?"
            r"(?:preferisco|mi piace|adoro|uso|ho bisogno di|voglio|evito|odio)\b",
            # German — "ich" required: the verbs are common in other persons.
            r"\bich (?:immer |normalerweise |wirklich |nur |nie )?"
            r"(?:bevorzuge|mag|liebe|benutze|brauche|will|vermeide|hasse)\b",
            # Polish
            r"\b(?:ja )?(?:zawsze |zwykle |naprawde |tylko |nigdy )?"
            r"(?:wole|lubie|uwielbiam|uzywam|potrzebuje|chce|unikam|nienawidze)\b",
            # Russian — pronoun required, for the same reason as German.
            r"\bя (?:всегда |обычно |действительно |только |никогда )?"
            r"(?:предпочитаю|люблю|нравится|использую|нужно|хочу|избегаю|ненавижу)\b",
        )
    ),
    re.IGNORECASE | re.UNICODE,
)

#: Function words, shared with recall so one list serves both. This module had its own English
#: thirteen; the shared one covers the eight alphabetic languages the app ships in.
_KNOWN_OVERLAP = 0.6  # a suggestion this much inside a stored fact is already known


def _significant(text: str) -> set[str]:
    """Meaningful tokens of ``text``, through the shared tokenizer and word list.

    The length rule keeps its exception for scripts where one character is a word — Han is split per
    character, and a three-character floor would discard all of them.
    """
    from chimera.memory.tokens import informative
    from chimera.memory.tokens import tokens as _shared

    return {t for t in informative(_shared(text)) if len(t) >= 3 or not t.isascii()}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _is_known(phrase: str, known_token_sets: list[set[str]]) -> bool:
    tokens = _significant(phrase)
    if not tokens:
        return True  # nothing meaningful to save
    return any(len(tokens & known) / len(tokens) >= _KNOWN_OVERLAP for known in known_token_sets)


def detect_nudges(
    user_texts: list[str], known_facts: list[str], *, max_suggestions: int = 3
) -> list[str]:
    """First-person preferences stated in ``user_texts`` that aren't already in memory.

    Returns each as the stated phrase (e.g. "I prefer async code"), deduped and capped at
    ``max_suggestions``. Empty when nothing new is worth saving.
    """
    known_token_sets = [_significant(fact) for fact in known_facts]
    seen: set[str] = set()
    suggestions: list[str] = []
    for text in user_texts:
        for sentence in re.split(r"[.\n;!?]+", text):
            # Matched against the FOLDED sentence and sliced out of the original one. The
            # patterns are written without diacritics, and run against raw text they silently
            # excluded every correctly written French and Polish sentence: `je prefere` matched,
            # `je préfère` did not. `fold_for_match` preserves length, so the offsets still cut
            # the original sentence in the right place.
            match = _PREFERENCE.search(fold_for_match(sentence))
            if match is None:
                continue
            phrase = sentence[match.start() :].strip().strip(",")
            norm = _normalize(phrase)
            if norm in seen or _is_known(phrase, known_token_sets):
                continue
            seen.add(norm)
            suggestions.append(phrase)
            if len(suggestions) >= max_suggestions:
                return suggestions
    return suggestions
