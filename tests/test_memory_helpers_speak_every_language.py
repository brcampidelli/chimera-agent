"""Consolidation and nudges, measured in the languages this app ships in.

Both modules carried their own ``[a-z0-9]+``, the same one recall had. The damage was not the same
in each, and the difference is the point:

* **Consolidation** was really blocked by the tokenizer. Two nearly identical Russian facts produced
  two empty token sets, so Jaccard overlap refused them. Measured before: English and Portuguese
  clustered, Russian and Chinese never did.

* **Nudges** was not. Repairing its tokenizer alone would have changed nothing measurable, because
  the pattern that decides whether a sentence is a preference rejects it before a token is taken —
  and that pattern was English. "eu prefiro respostas curtas", "prefiero respuestas cortas",
  "je préfère des réponses courtes", "ich bevorzuge kurze Antworten" and "я предпочитаю краткие
  ответы" all returned NOTHING. A fix that fixes nothing is worth naming rather than shipping.

The false-positive half is the one that matters most here: nudges suggests text to SAVE as a fact
about the person, so admitting somebody else's preference is worse than missing their own.
"""

from __future__ import annotations

import pytest

from chimera.memory.consolidate import group_similar
from chimera.memory.nudges import detect_nudges


def _grouped(textos: list[str]) -> bool:
    return any(len(g) > 1 for g in group_similar(textos, threshold=0.5))


# --- consolidation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lingua", "textos"),
    [
        ("en", ["the user prefers concise answers in chat",
                "the user prefers concise answers when chatting"]),
        ("pt", ["o usuario prefere respostas concisas no chat",
                "o usuario prefere respostas concisas quando conversa"]),
        ("pt-acentos", ["a autenticação do usuário usa OAuth no projeto",
                        "a autenticação do usuário é por OAuth neste projeto"]),
        ("ru", ["пользователь предпочитает краткие ответы в чате",
                "пользователь предпочитает краткие ответы при общении"]),
        ("zh", ["用户喜欢在聊天中得到简洁的回答", "用户喜欢聊天时得到简洁回答"]),
    ],
)
def test_near_identical_facts_cluster_in_every_script(lingua: str, textos: list[str]) -> None:
    """Russian and Chinese produced empty token sets, so nothing ever merged for those users."""
    assert _grouped(textos), f"{lingua}: two nearly identical facts did not cluster"


@pytest.mark.parametrize(
    ("lingua", "textos"),
    [
        ("en", ["the user prefers concise answers", "the project lives in the documents folder"]),
        ("pt", ["o usuario prefere respostas curtas", "o projeto fica na pasta documentos"]),
        ("ru", ["пользователь предпочитает краткие ответы", "проект находится в папке документы"]),
        ("zh", ["用户喜欢简洁的回答", "项目在文档文件夹中"]),
    ],
)
def test_unrelated_facts_still_do_not_cluster(lingua: str, textos: list[str]) -> None:
    """The other half of the trade. Making everything tokenize must not make everything merge.

    Non-Latin tokens are kept regardless of length, because Han is split per character — so the
    control matters more here than usual, since short function words survive the length rule.
    """
    assert not _grouped(textos), f"{lingua}: unrelated facts were merged into one"


# --- nudges -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lingua", "frase"),
    [
        ("en", "i prefer short answers"),
        ("pt", "eu prefiro respostas curtas"),
        ("es", "prefiero respuestas cortas"),
        ("fr", "je préfère des réponses courtes"),
        ("it", "preferisco risposte brevi"),
        ("de", "ich bevorzuge kurze Antworten"),
        ("pl", "wolę krótkie odpowiedzi"),
        ("ru", "я предпочитаю краткие ответы"),
    ],
)
def test_a_stated_preference_is_offered_in_every_shipped_language(lingua: str, frase: str) -> None:
    """Only English produced a suggestion before. The rest produced none, ever.

    The accented spellings are the real ones on purpose: the patterns are written without
    diacritics, and matched against raw text they excluded correctly written French and Polish —
    `je prefere` matched and `je préfère` did not.
    """
    assert detect_nudges([frase], [], max_suggestions=2), f"{lingua}: no suggestion offered"


@pytest.mark.parametrize(
    ("porque", "frase"),
    [
        ("pt third person", "o cliente prefere respostas curtas"),
        ("pt question", "voce prefere qual abordagem?"),
        ("en third person", "the team prefers tabs"),
        ("en simile", "this works like a charm"),
        ("de third person", "er bevorzugt kurze antworten"),
        ("ru third person", "он предпочитает краткие ответы"),
        ("pt instruction to the agent", "use o modo escuro neste projeto"),
    ],
)
def test_what_is_not_the_persons_own_preference_is_not_offered(porque: str, frase: str) -> None:
    """This is the half that matters: a suggestion here becomes a stored fact ABOUT THE PERSON.

    Two of these were real regressions while the patterns were being written. French drops no
    subject pronoun, so an optional `je` let the folded `prefere` match Portuguese third person and
    Portuguese questions — offering to save somebody else's preference as the user's own.
    """
    assert detect_nudges([frase], [], max_suggestions=2) == [], f"{porque}: offered {frase!r}"
