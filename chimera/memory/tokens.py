"""How a query and a stored fact are cut into comparable pieces, and which pieces mean anything.

Every rule here replaced one that was measured wrong on a real install.

**The alphabet.** The tokenizer was ``[a-z0-9]+`` over lowercased text, so it saw only ASCII::

    "где находится проект"  -> []          (Russian)
    "项目在哪里"              -> []          (Chinese)
    "プロジェクトはどこ"        -> []          (Japanese)

Three of the ten languages this app ships in. Keyword memory recall returned nothing for them, on
every query, for ever — the feature was not degraded, it was absent, and silently.

**The accents.** For the languages it did see, the same class did not merely drop accents, it
*manufactured* tokens at them: ``"autenticação" -> ['autentica', 'o']``. Every ``-ção`` produced an
``o``, which is exactly the token that matches everything. Writing correct Portuguese made recall
worse; typing English never met the defect.

**The meaning.** Scoring was ``len(query & fact)`` kept when non-zero — one shared token, any token.
Against a real store of two facts, *"compile o kernel com suporte a NUMA e verifique o dmesg"*
recalled BOTH, on ``o``, ``com``, ``a``, ``e``.

Inverse document frequency was tried first and **measured inert**: with two stored facts, ``o``
appears in one of them, so IDF rates it maximally informative. A corpus that small carries no
evidence about which words are common — the statistic needs documents it does not have. It is kept
below for RANKING, where it earns its place as the store grows, but it cannot be the filter.

A rejection floor of ``log(2)`` was tried on top of it and dropped, measured: a query whose only
real word appears in every stored fact (*"how should answers be?"* against two facts that both
mention answers) scored exactly zero and recalled nothing at all.

What can is a stopword list, and the honest version of one covers every language the app ships in
rather than the one whose defect was noticed. That is the same rule this project wrote down for the
website's alpha caveat: narrow the rule, never except the languages.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence

__all__ = ["fold_for_match", "idf_weights", "informative", "tokens"]

#: Any letter or digit in any script, after folding. `[^\W_]` rather than a named list of ranges:
#: a range list is a promise to remember every alphabet, and this file exists because one was
#: forgotten. Underscore is excluded so `snake_case` splits into words.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

#: Function words for the languages this app ships in, written without diacritics because that is
#: what `tokens` produces. Closed-class only — articles, prepositions, pronouns, conjunctions and
#: the commonest auxiliaries. A content word must never be here: removing one makes a fact
#: unfindable, which is a worse failure than admitting one noisy hit.
#:
#: Chinese and Japanese are absent on purpose. Chinese is split per character below, so a particle
#: is indistinguishable from a character carrying meaning inside a compound; Japanese kana runs stay
#: whole, so a particle is glued to the word beside it rather than standing alone to be removed.
_BY_LANGUAGE: dict[str, str] = {
    "en": (
        "a an the and or but if of to in on at by for with from as is are was were be been being "
        "this that these those it its he she they we you i my your his her their our me him them us "
        "do does did done can could will would shall should may might must not no yes so than then "
        "there here what which who whom whose when where why how all any both each few more most "
        "other some such only own same too very just about into over under again further once"
    ),
    "pt": (
        "o a os as um uma uns umas de do da dos das em no na nos nas por para com sem sob sobre e ou "
        "mas se que ao aos as isso isto este esta esses essas aquele aquela meu minha seu sua nosso "
        "nossa ser estar ter foi sao esta estao nao sim ja mais menos muito pouco todo toda todos "
        "todas entao quando onde como qual quem porque eu tu ele ela nos eles elas voce voces me te "
        "lhe lhes ha havia sera seria tambem ainda apos ate depois antes durante"
    ),
    "es": (
        "el la los las un una unos unas de del al en por para con sin sobre y o pero si que se es "
        "son era eran ser estar tener no si ya mas menos muy todo toda todos todas cuando donde como "
        "cual quien porque yo tu el ella nosotros ellos ellas usted ustedes me te le les hay tambien "
        "aun hasta despues antes durante entre"
    ),
    "fr": (
        "le la les un une des du de au aux en dans sur sous pour par avec sans et ou mais si que qui "
        "quoi dont ou est sont etait etaient etre avoir ne pas plus moins tres tout toute tous "
        "toutes quand comment pourquoi je tu il elle nous vous ils elles me te se lui leur y il "
        "aussi encore apres avant pendant entre"
    ),
    "de": (
        "der die das den dem des ein eine einen einem eines und oder aber wenn dass zu in an auf aus "
        "bei mit nach von vor uber unter fur ohne ist sind war waren sein haben hat hatte nicht kein "
        "keine mehr weniger sehr alle alles wann wo wie warum ich du er sie es wir ihr mich dich "
        "sich uns euch auch noch schon dann denn als"
    ),
    "it": (
        "il lo la i gli le un uno una di del della dei delle da in su per con senza e o ma se che "
        "chi cui e sono era erano essere avere non piu meno molto tutto tutta tutti tutte quando "
        "dove come perche io tu lui lei noi voi loro mi ti si ci vi anche ancora dopo prima durante "
        "tra fra"
    ),
    "pl": (
        "i oraz albo lub ale jesli ze w we na do od za po pod nad przy bez dla o u z ze jest sa byl "
        "byla bylo byc miec nie tak juz wiecej mniej bardzo wszystko wszyscy kiedy gdzie jak "
        "dlaczego ja ty on ona ono my wy oni one mnie ciebie siebie nas was tez jeszcze"
    ),
    # Cyrillic, which the tokenizer could not even see before this file existed.
    "ru": (
        "и или но если что как когда где почему в во на к ко с со из от до по за под над при "
        "без для о об у я ты он она оно мы вы они меня тебя себя нас вас их его ее это этот "
        "эта эти тот та те не нет да уже еще очень все весь вся был была было быть есть также "
        "тоже"
    ),
}

#: One flat set. The split lives here, on a variable, rather than on each literal above.
_STOPWORDS = frozenset(w for words in _BY_LANGUAGE.values() for w in words.split())


def _fold(text: str) -> str:
    """Lowercase, and drop diacritics from LATIN letters only.

    The restriction is not caution, it is a measured correction. Folding the whole string turned
    ``プロジェクト`` into ``フロシェクト``: the dakuten on ``プ`` and ``ジ`` decomposes like an accent
    and is nothing of the sort — ``は`` / ``ば`` / ``ぱ`` are three different letters. The same
    argument holds for Cyrillic ``й`` and Greek ``ά``.

    So the fold applies where accent-insensitive matching is what people actually expect, and where
    the defect that prompted this lived: ``ç`` and ``ã`` in Latin script.
    """
    out: list[str] = []
    for ch in text.lower():
        decomposed = unicodedata.normalize("NFKD", ch)
        base = decomposed[0] if decomposed else ch
        if "a" <= base <= "z" or "0" <= base <= "9":
            out.append("".join(c for c in decomposed if not unicodedata.combining(c)))
        else:
            out.append(ch)
    return "".join(out)


def fold_for_match(text: str) -> str:
    """``text`` with Latin diacritics folded, and EXACTLY the same length.

    For matching a pattern against prose and then slicing the ORIGINAL string by the match's
    offsets. `_fold` is free to change length (a ligature decomposes into two characters); here a
    character whose fold is not exactly one character is left alone, because a shifted offset would
    cut a sentence in the wrong place — and the sentence is what gets shown to the user and saved.
    """
    out: list[str] = []
    for ch in text:
        decomposed = unicodedata.normalize("NFKD", ch.lower())
        base = decomposed[0] if decomposed else ch
        if "a" <= base <= "z":
            folded = "".join(c for c in decomposed if not unicodedata.combining(c))
            out.append(folded if len(folded) == 1 else ch)
        else:
            out.append(ch)
    return "".join(out)


def _han(ch: str) -> bool:
    """A Han ideograph — the script where one character is roughly one unit of meaning."""
    code = ord(ch)
    return 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF


def tokens(text: str) -> list[str]:
    """Comparable pieces of ``text``: any script, Latin diacritics folded, Han split per character.

    Folding rather than widening the character class does two jobs: it stops ``ç`` and ``ã`` from
    splitting a word into fragments plus a stray vowel, and it makes ``voce`` match ``você`` —
    which matters because people type both, and a store that distinguishes them remembers half of
    what it was told.
    """
    out: list[str] = []
    for match in _WORD.finditer(_fold(text)):
        buffer = ""
        for ch in match.group():
            if _han(ch):
                # One character, one token. Chinese puts no spaces between words, so a whole
                # sentence arrives as a single run; per-character is the cheap segmentation that
                # lets a query for 项目 reach a fact containing 项目在哪里.
                if buffer:
                    out.append(buffer)
                    buffer = ""
                out.append(ch)
            else:
                buffer += ch
        if buffer:
            out.append(buffer)
    return out


def informative(terms: Iterable[str]) -> set[str]:
    """``terms`` with function words removed. Empty when every one of them was a function word.

    The first version fell back to the words themselves when stripping left nothing, on the
    reasoning that an empty set must not silently mean "no filter". Measured, that fallback
    reproduced the defect it was meant to guard: *"o que e isso?"* recalled both stored facts.

    Empty is the honest answer. A question built only of function words asks for nothing in
    particular, and the caller reads an empty set as "nothing to search for" rather than as
    "everything matches".
    """
    return {t for t in terms if t not in _STOPWORDS}


def idf_weights(terms: Iterable[str], documents: Sequence[Sequence[str]]) -> dict[str, float]:
    """How much each of ``terms`` distinguishes one of ``documents`` from the others.

    ``log(n / df)``: zero for a term in every document, largest for one in a single document.
    Returns weights only for terms that appear somewhere — an unseen term cannot inform.

    Empty below two documents, and the caller falls back to plain overlap: one fact cannot make
    another common, and zeros there would mean a store holding a single memory recalls nothing.
    """
    n = len(documents)
    if n < 2:
        return {}
    df: dict[str, int] = {}
    for doc in documents:
        for token in set(doc):
            df[token] = df.get(token, 0) + 1
    return {t: math.log(n / df[t]) for t in set(terms) if df.get(t)}
