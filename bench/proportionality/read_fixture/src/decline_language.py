"""The sentence a grounded answer ships when the sources do not cover the question, in the owner's
language.

The product's single language rule (study 25, L0) is the owner's: ``agent.json``'s ``language``
("Português (Brasil)", "日本語"), which the prompt stack renders as "Always answer in …, whatever
language the question … is in" (`chimera/core/instructions.py::render`). A decline written by our
code — not by the model — has to follow the same rule or it is the one English sentence in a
Portuguese conversation. So:

1. the owner's ``language``, when it names one of the ten desktop locales (in English or in its own
   name — "Portuguese", "Português (Brasil)", "pt-BR" all read as ``pt``);
2. otherwise the question's language, read by function words and script, no model;
3. otherwise English.

The table holds the ten desktop locales (``apps/desktop/src/lib/i18n.tsx``), each a translation of
the same sentence, not a paraphrase.
"""

from __future__ import annotations

import re
import unicodedata

DECLINES: dict[str, str] = {
    "en": "The sources provided don't cover this.",
    "pt": "As fontes fornecidas não cobrem isso.",
    "es": "Las fuentes proporcionadas no cubren esto.",
    "fr": "Les sources fournies ne couvrent pas cela.",
    "de": "Die bereitgestellten Quellen decken das nicht ab.",
    "it": "Le fonti fornite non coprono questo.",
    "pl": "Dostarczone źródła tego nie obejmują.",
    "ru": "Предоставленные источники этого не охватывают.",
    "zh": "所提供的来源不涵盖这一点。",
    "ja": "提供された情報源ではカバーされていません。",
}

#: What an owner may type in the identity's language field, per locale: codes, English names,
#: native names. Matched as a prefix of a word after case-folding (a two-letter code: the whole word).
_NAMES: dict[str, tuple[str, ...]] = {
    "en": ("en", "english", "ingles", "inglés", "inglês"),
    "pt": ("pt", "portug", "brasil", "brazil"),
    "es": ("es", "spanish", "espanol", "español", "espanhol", "castellano"),
    "fr": ("fr", "french", "francais", "français", "francês"),
    "de": ("de", "german", "deutsch", "alemao", "alemão"),
    "it": ("it", "italian", "italiano"),
    "pl": ("pl", "polish", "polski", "polonês", "polones"),
    "ru": ("ru", "russian", "russo", "русск"),
    "zh": ("zh", "chinese", "chinês", "chines", "中文", "汉语", "漢語", "普通话"),
    "ja": ("ja", "japanese", "japonês", "japones", "日本語"),
}

#: Function words per Latin-script language, for a question with no owner language to go on.
_MARKERS: dict[str, tuple[str, ...]] = {
    "en": ("the", "is", "are", "what", "how", "does", "do", "which", "who", "of", "to", "in", "and", "this"),
    "pt": ("o", "os", "as", "que", "não", "é", "em", "um", "uma", "qual", "quem", "você", "isso", "são", "há", "do", "da",
           "de", "por", "para", "no", "na", "com", "existe", "mês", "está"),
    "es": ("el", "los", "las", "qué", "cuál", "cuánto", "es", "está", "una", "del", "por", "este", "hay", "cómo"),
    "fr": ("le", "les", "la", "est", "que", "quel", "quelle", "une", "des", "du", "pour", "combien", "comment", "ce"),
    "de": ("der", "die", "das", "ist", "was", "wie", "welche", "ein", "eine", "nicht", "und", "für", "gibt"),
    "it": ("il", "gli", "che", "è", "qual", "quale", "quanto", "una", "della", "del", "per", "come", "cosa"),
    "pl": ("jest", "czy", "jaki", "jaka", "co", "nie", "się", "ile", "który", "która", "jak", "dla", "na"),
}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").casefold().strip()


def from_owner(language: str) -> str:
    """The locale code the owner's language field names, or ``""`` when it names none of the ten."""
    words = re.split(r"[\s()\-_/,;]+", _fold(language))
    for code, names in _NAMES.items():
        for word in words:
            if word and any((word == n if len(n) <= 2 else word.startswith(n)) for n in names):
                return code
    return ""


def from_text(text: str) -> str:
    """The question's language among the ten, or ``""`` when it cannot be told."""
    low = _fold(text)
    if re.search(r"[぀-ヿ]", low):
        return "ja"
    if re.search(r"[一-鿿]", low):
        return "zh"
    if re.search(r"[Ѐ-ӿ]", low):
        return "ru"
    words = re.findall(r"[\wà-ÿąćęłńóśźż]+", low)
    if not words:
        return ""
    counts = {code: sum(1 for w in words if w in set(m)) for code, m in _MARKERS.items()}
    best = max(counts, key=lambda c: counts[c])
    if counts[best] == 0 or list(counts.values()).count(counts[best]) > 1:
        return ""
    return best


def decline_language(owner_language: str, question: str) -> str:
    """The locale the decline is written in: the owner's, else the question's, else English."""
    return from_owner(owner_language) or from_text(question) or "en"


def decline_text(owner_language: str, question: str) -> str:
    return DECLINES[decline_language(owner_language, question)]
