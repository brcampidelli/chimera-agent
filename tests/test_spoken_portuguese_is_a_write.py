"""How a request written the way people talk gets classified.

`_WRITE_MARKERS` had a Portuguese half, and it was written in the imperative-formal register a
specification is written in: *escreva*, *implemente*, *modifique*. Nobody types that at an app.

So "faz um site pra minha padaria" — the shape of request this product exists for, in the language
its interface defaults to — carried no marker at all and came out `simple`. The orchestration screen
would offer to send it to a single agent, and the shape that routes writing work to a crew of
isolated workers was reachable only by someone who phrased their request like a ticket.

The English half never had this problem, because its markers ("create ", "write ", "fix ") are the
words English speakers actually use. This is the same defect the memory tokenizer had: a rule
calibrated on English, passing on English, failing everywhere else.
"""

from __future__ import annotations

import pytest

from chimera.orchestration.hierarchy import classify_task

#: How people open a request out loud: first person, or a spoken imperative.
FALADO = [
    "faz um site pra minha padaria",
    "faça uma página de contato",
    "cria um app de lista de tarefas",
    "monta uma apresentação sobre o produto",
    "quero um blog simples",
    "preciso de um formulário de cadastro",
    "gostaria de um site institucional",
    "me faz um botão de download",
    "adiciona um rodapé com o telefone",
    "arruma o espaçamento do cabeçalho",
    "muda a cor do menu",
    "apaga o arquivo antigo",
    "gera um relatório em html",
]


@pytest.mark.parametrize("pedido", FALADO)
def test_a_spoken_request_to_build_is_a_write(pedido: str) -> None:
    assert classify_task(pedido) == "sequential_write", (
        f"{pedido!r} is somebody asking for something to be built"
    )


#: The control. Widening the write markers must not swallow reading work — that would send tasks
#: that genuinely parallelise to a single writer, which is the opposite failure and just as quiet.
LEITURA = [
    ("leia index.html e sobre.html e compare os dois", "parallel_read"),
    ("compare notas-a.md e notas-b.md", "parallel_read"),
    ("o que é HTML?", "simple"),
    ("me explica o que esse projeto faz", "simple"),
]


@pytest.mark.parametrize(("pedido", "esperado"), LEITURA)
def test_reading_work_is_still_reading_work(pedido: str, esperado: str) -> None:
    assert classify_task(pedido) == esperado


def test_a_word_that_merely_contains_a_marker_is_not_a_write() -> None:
    """The cost of substring matching, bounded on purpose.

    The English half matches substrings too, so this is the existing convention rather than a new
    risk — but the markers were chosen with it in mind. "criatura" and "faz" inside "desfazer" are
    the collisions worth naming; the trailing space on the loosest ones is what keeps them apart.
    """
    # `crie ` and `cria ` carry a trailing space, so a noun that starts the same way does not match.
    assert classify_task("me fale sobre criaturas marinhas") != "sequential_write"
