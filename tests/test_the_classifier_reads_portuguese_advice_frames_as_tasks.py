"""Advice, appraisal and polite frames read as tasks in Portuguese as well as English.

The fresh set (`bench/grounded_question_classifier/RESULTS_fresh.md`) found five tasks read as
questions. Four were Portuguese and their English twins passed: the rules v2 widened on the held-out
set had reached one language only. Each message below stands for a class the fix names, not a
string it matches. These five are now in-sample; a new unseen set is what says whether the classes
generalize.
"""

from __future__ import annotations

import pytest

from chimera.fusion.grounded_question import is_question


@pytest.mark.parametrize(
    "message",
    [
        # "será que" in front of a polite frame and a task verb
        "Será que dá pra reescrever este e-mail para ficar cordial, mas sem prometer um reembolso?",
        # a second-person conditional asks for the assistant's own view
        "O que você mudaria neste currículo para uma vaga de analista de dados?",
        # appraisal ("adequada") and a task verb ("justifique")
        "A proposta de investimento parece adequada ao perfil conservador descrito no formulário? "
        "Justifique a avaliação.",
        # help deciding is advice
        "Me ajuda a decidir como proceder com essa cobrança? Considere as informações da carta e "
        "apresente opções.",
        # appraisal ("risky") behind a "tell me"
        "Tell me whether the construction schedule seems risky and what dependencies could cause delays.",
    ],
)
def test_an_advice_or_appraisal_frame_is_a_task(message: str) -> None:
    assert not is_question(message)


@pytest.mark.parametrize(
    "message",
    [
        "Qual é o valor da multa?",
        "Me passa o CNPJ da contratada?",
        "A cláusula de confidencialidade tem prazo de vigência?",
        "Você poderia me dizer o nome da escola indicada no histórico?",
        "Você saberia me dizer a data de emissão do relatório?",
        "What interest rate is stated in the loan offer?",
        "Does the medical report state the size of the lesion?",
    ],
)
def test_a_plain_question_about_the_document_is_still_a_question(message: str) -> None:
    assert is_question(message)
