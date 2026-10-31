"""Which requests need commands, decided by a table and not by a model.

The turn asks the owner before the agent edits anything when the folder has commands off and the
request needs them. The table is the contract: a missed row is an hour of work with nothing
verified, and a false row is one question the owner can answer "continue" to.
"""

from __future__ import annotations

import pytest

from chimera.core.command_intent import is_plain_question, request_needs_commands


@pytest.mark.parametrize(
    "message",
    [
        "rode os testes e corrija o que falhar",
        "rode o pytest no módulo de faturas",
        "Rodar o build antes de mergear",
        "execute o lint no projeto",
        "verifique se o typecheck passa",
        "implementar o cache de sessão",
        "corrija o bug do login",
        "npm install e depois o build",
        "rode vitest em apps/desktop",
        "run the test suite",
        "please run pytest",
        "fix the failing build",
        "implement the retry loop and run it",
        "Fix this bug in parser.py",
        "lint the whole repo",
        "typecheck it",
        "Pode rodar os testes?",
        "Consegue consertar o login?",
        "Como está o build? Rode de novo.",
        "Por que o teste falha? Corrija.",
        "Qual o status? Faça o deploy.",
        "Consegue rodar o build?",
        "Pode executar o pytest?",
        "Você pode criar um teste para isso?",
        "teste a função de parse",
        "conserte o parser",
        "rode o mypy no pacote",
        "rode o ruff antes do commit",
        "rode o jest no painel",
        "compile o projeto",
        "instale as dependências",
        "faça o deploy da release",
        "debugue o erro de conexão",
        "checar o resultado do job",
        "valide o schema do banco",
        "altere o arquivo de config",
        "ajuste o método de retry",
        "crie o módulo de cache",
        "refatore o store de sessões",
        "edite o README",
        "remova o import morto",
        "adicione um comentário no topo do arquivo",
    ],
)
def test_a_command_word_means_the_request_needs_commands(message: str) -> None:
    assert request_needs_commands(message)


@pytest.mark.parametrize(
    "message",
    [
        "o que faz a função parse_date?",
        "Explique como funciona o cache.",
        "qual é a capital da França?",
        "what does this module do?",
        "how does the router pick a model?",
        "explain the design of the store",
        "Quais são os principais riscos do plano?",
        "qual é o bug no login?",
        "o que executa esta função?",
        "explique o build",
        "renomeie a variável x para total",
        "mude o título da página",
    ],
)
def test_a_question_or_plain_edit_without_a_command_word_does_not_need_commands(message: str) -> None:
    assert not request_needs_commands(message)


@pytest.mark.parametrize(
    "message",
    [
        "renomeie a variável x para total",
        "mude o nome do arquivo de config",
    ],
)
def test_a_project_with_a_verify_command_makes_an_action_request_need_commands(message: str) -> None:
    assert request_needs_commands(message, verify_configured=True)


@pytest.mark.parametrize(
    "message",
    [
        "o que faz a função parse_date?",
        "explain the design of the store",
        "Explique como funciona o cache.",
        "qual é o bug no login?",
        "o que executa esta função?",
        "explique o build",
    ],
)
def test_a_plain_question_never_needs_commands_even_with_a_verify_command(message: str) -> None:
    assert not request_needs_commands(message, verify_configured=True)


@pytest.mark.parametrize(
    "message",
    [
        "rodovia do plano",
        "a executiva da empresa",
        "o testamento do cliente",
    ],
)
def test_words_that_only_share_a_stem_do_not_match(message: str) -> None:
    assert not request_needs_commands(message)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("o que é isto?", True),
        ("  Explique o módulo", True),
        ("como funciona", True),
        ("what is a test?", True),
        ("renomeie o arquivo", False),
        ("", False),
    ],
)
def test_is_plain_question_reads_the_question_mark_and_the_opening_word(
    message: str, expected: bool
) -> None:
    assert is_plain_question(message) is expected
