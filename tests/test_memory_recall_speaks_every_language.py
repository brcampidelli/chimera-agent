"""Keyword recall, measured on a real install and found wrong three ways.

The store under test is the one a person actually had in the desktop app: two facts, one about a
project and one about how they like to be answered. Every case below was run against it before any
code changed, and the numbers in the comments are what came back.

The three defects were independent:

1. ``[a-z0-9]+`` saw only ASCII, so recall returned NOTHING for Russian, Chinese and Japanese — three
   of the ten languages the app ships in. Not degraded: absent, silently, always.
2. The same class manufactured tokens at accents (``"autenticação" -> ['autentica', 'o']``), so
   writing correct Portuguese produced the very token that matches everything.
3. One shared token, any token, was a hit — so a sentence about compiling a kernel recalled both
   stored facts on ``o``, ``com``, ``a``, ``e``.

Inverse document frequency was implemented first for (3) and **measured inert**: with two documents,
``o`` appears in one of them and so scores as maximally rare. That negative is worth a test of its
own, below, so nobody removes the stopword list believing IDF covers it.
"""

from __future__ import annotations

from pathlib import Path

from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore
from chimera.memory.tokens import informative, tokens

#: The two facts that were really in the app, verbatim.
PROJETO = "O projeto de teste do Cafe Aurora fica em Desktop/teste-chimera/site-institucional"
ESTILO = "Prefiro respostas curtas e diretas, sem rodeios"


def _real_store(tmp_path: Path) -> MemoryManager:
    mgr = MemoryManager(MemoryStore(tmp_path / "mem.json"))
    mgr.remember(PROJETO)
    mgr.remember(ESTILO, "persona")
    return mgr


# --- the alphabet -------------------------------------------------------------------------------


def test_recall_sees_scripts_that_are_not_ascii() -> None:
    """Russian, Chinese and Japanese all came back as zero tokens, which is zero recall for ever."""
    assert tokens("где находится проект"), "Russian tokenizes to nothing"
    assert tokens("项目在哪里"), "Chinese tokenizes to nothing"
    assert tokens("プロジェクトはどこ"), "Japanese tokenizes to nothing"
    assert tokens("που ειναι το εργο"), "Greek tokenizes to nothing"


def test_han_is_split_per_character_so_a_query_can_reach_a_sentence() -> None:
    """Chinese writes no spaces, so a whole clause arrives as one run and would match only itself."""
    fato = set(tokens("项目在哪里"))
    assert set(tokens("项目")) <= fato, "a query for 项目 cannot reach a fact containing it"


def test_kana_keeps_its_dakuten() -> None:
    """A fix for one script must not damage another.

    Folding diacritics across the whole string turned ``プロジェクト`` into ``フロシェクト``: the
    dakuten decomposes like an accent and is nothing of the sort. This is the guard for that
    regression, which was introduced and caught in the same sitting.
    """
    assert tokens("プロジェクト") == ["プロジェクト"]
    assert tokens("ば") != tokens("は"), "ば and は collapsed into the same token"


# --- the accents --------------------------------------------------------------------------------


def test_accents_do_not_manufacture_tokens() -> None:
    """`autenticação` produced an `o` that nobody wrote, and an `o` matches everything."""
    assert tokens("autenticação") == ["autenticacao"]
    assert tokens("coordenação") == ["coordenacao"]
    assert tokens("você") == ["voce"]


def test_the_same_word_typed_with_and_without_accents_is_the_same_word() -> None:
    """People type both, and a store that distinguishes them remembers half of what it was told."""
    assert tokens("a autenticação do usuário") == tokens("a autenticacao do usuario")


# --- the meaning --------------------------------------------------------------------------------


def test_a_sentence_sharing_only_function_words_recalls_nothing(tmp_path: Path) -> None:
    """Measured before: recalled the project fact. On `o` and `de`."""
    mgr = _real_store(tmp_path)
    assert mgr.search("corrija o erro de tipo neste arquivo", k=3) == []
    assert mgr.search("explique o que este codigo faz", k=3) == []


def test_a_sentence_that_shares_real_words_still_recalls(tmp_path: Path) -> None:
    """The other half of the trade. Removing noise must not remove the signal."""
    mgr = _real_store(tmp_path)

    projeto = mgr.search("onde fica o projeto do Cafe Aurora?", k=3)
    assert projeto and PROJETO in projeto[0].content

    estilo = mgr.search("prefiro respostas curtas?", k=3)
    assert estilo and ESTILO in estilo[0].content


def test_idf_alone_does_not_catch_this_which_is_why_the_word_list_exists() -> None:
    """The negative result, kept so the list is not removed as redundant later.

    With two documents, ``o`` appears in one of them — so ``log(n/df)`` rates it exactly as
    informative as ``aurora``, a word that appears in one fact and nowhere else. A statistic about
    how common a word is needs documents it does not have here.
    """
    from chimera.memory.tokens import idf_weights

    docs = [tokens(PROJETO), tokens(ESTILO)]
    pesos = idf_weights({"o", "aurora"}, docs)

    assert pesos["o"] == pesos["aurora"], (
        "IDF now separates `o` from `aurora` on a two-document store; if that is really true the "
        "stopword list may be reconsidered — it was not true when this was written"
    )


def test_a_word_common_to_every_fact_still_recalls_them(tmp_path: Path) -> None:
    """Why there is no rejection floor, and the case that removed the one there was.

    A floor of ``log(2)`` was added first: it read as "the match must carry at least as much
    information as a term appearing in at most half the store". It also rejected this, where the
    query's only real word is in every fact — score exactly zero, recall nothing. Both facts are
    about answers and the person asked about answers; returning them is the useful answer.
    """
    mgr = MemoryManager(MemoryStore(tmp_path / "m.json"))
    mgr.remember("answers should be short")
    mgr.remember("the user prefers concise answers")

    assert mgr.search("how should answers be?", k=3), (
        "a word shared by every stored fact now recalls nothing — the rejection floor is back"
    )


def test_a_query_of_only_function_words_recalls_nothing(tmp_path: Path) -> None:
    """The first fix fell back to the stripped words here, and that reproduced the whole defect.

    "o que e isso?" recalled both stored facts, which is exactly what removing function words was
    for. A question built only of function words asks for nothing in particular, and empty is the
    honest answer.
    """
    mgr = _real_store(tmp_path)
    assert mgr.search("o que e isso?", k=3) == []
    assert informative(["o", "que", "e"]) == set()


# --- the other backend ---------------------------------------------------------------------------


def test_the_sqlite_backend_answers_the_same_way(tmp_path: Path) -> None:
    """It has its own `search`, so it bypassed all three fixes.

    Measured with the JSON store already fixed: "o que e isso?" recalled nothing there and every
    fact in the store here. Which backend the owner picked is not supposed to change what a query
    means.
    """
    from chimera.memory.sqlite_store import SqliteMemoryStore

    mgr = MemoryManager(SqliteMemoryStore(tmp_path / "mem.db"))
    mgr.remember(PROJETO)
    mgr.remember(ESTILO, "persona")
    mgr.remember("проект находится в папке документы")
    mgr.remember("项目在文档文件夹中")

    assert mgr.search("o que e isso?", k=5) == [], "function words still recall everything"
    assert mgr.search("onde fica o projeto do Café Aurora?", k=5), "accented query finds nothing"
    assert mgr.search("где проект", k=5), "Russian finds nothing"
    assert mgr.search("项目", k=5), "Chinese finds nothing"
