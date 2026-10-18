"""The design-intent detector: explicit asks open, visual design suggests, code design stays silent.

`chimera/design/intent.py`. The owner asked (2026-10-17) that naming Chimera Design, asking to use
it, working on the design of a project, or wanting a task or a plan presented through design should
bring the tool. "Design" alone is also code design, system design and database design — most of what
this product is asked about — so the plan settled on three levels (PLANO.md, "Acionamento"), and this
table is that decision written down case by case. A case moved from one level to another is a change
of product behaviour, not a test fix.

The table is read whole as well as case by case: the last test prints precision and recall for each
level, so a pattern change that trades a few false alarms for a few hits shows up as numbers.
"""

from __future__ import annotations

import time

import pytest

from chimera.design.intent import MAX_CHARS, detect, fold

EXPLICIT: list[str] = [
    # pt — named, asked for, presented through
    "Abre o Chimera Design",
    "quero usar o chimera design pra isso",
    "roda o Chimera Design com esse briefing",
    "usa o design pra montar a capa",
    "rode o design",
    "abre o design agora",
    "apresenta como design o resultado da análise",
    "apresentar em design, por favor",
    "mostra o plano em design",
    "mostre esse roadmap como design",
    "plano em design",
    "faz o plano de lançamento em design",
    "abre a ferramenta de design",
    "coloca isso no design",
    "Chimera-Design: crie um post",
    # en
    "open Chimera Design",
    "use chimera design for the pitch",
    "run the design tool",
    "launch the design editor",
    "present this as a design",
    "show me the plan in design",
    "present the roadmap as a design please",
    "use the design mode and draft the deck",
    "turn the plan into design",
    # es
    "abre Chimera Design",
    "usa el design para esto",
    "presenta el plan como design",
    "muestra la propuesta en design",
    "ejecuta la herramienta de diseño",
    "plan en design",
]

SUGGEST: list[str] = [
    # pt
    "faz um design pra landing page do PassaPro",
    "preciso do design das telas de onboarding",
    "melhora o design desse slide",
    "design de um post para o Instagram",
    "quero um design de logo minimalista",
    "crie o design do banner da Black Friday",
    "revisa o design da apresentação do investidor",
    "design de um mockup do app",
    "um wireframe com design limpo",
    "design da interface do painel",
    "o design do flyer está feio",
    "ajusta o design da UI do checkout",
    "trabalhando no design do protótipo",
    "design do cartaz do evento",
    "atualiza o design system com as cores novas",
    # en
    "design a landing page for the launch",
    "can you design the onboarding screens",
    "improve the design of this slide deck",
    "I need a poster design",
    "design a logo for my bakery",
    "the UI design of the settings screen",
    "a mockup design for the pricing page",
    "design the presentation for Monday",
    # es
    "diseño de la landing page",
    "haz un diseño para el banner",
    "necesito diseñar la pantalla de inicio",
    "diseño de un póster para el concierto",
    "el diseño de la presentación del cliente",
    "diseño de la interfaz móvil",
    "un diseño de logotipo",
]

NONE: list[str] = [
    # the owner's own negatives
    "design pattern",
    "system design",
    "design do banco de dados",
    "refatorar o design da API",
    "o design do código",
    # more technical design
    "qual design pattern usar para esse repositório?",
    "explique system design para entrevistas",
    "revise o design de software do módulo de pagamentos",
    "o design dos dados está normalizado?",
    "faça um design doc do serviço de filas",
    "domain-driven design no backend",
    "database design for the orders table",
    "api design review for the v2 endpoints",
    "the design of the class hierarchy is wrong",
    "use the design pattern from the other repo",
    "patrones de diseño en Python",
    "diseño de la base de datos de pedidos",
    "o design da arquitetura com microsserviços",
    "design by contract in Eiffel",
    "high-level design of the scheduler",
    # a show verb near "design" is not a request to present through it when the design is technical
    "mostre os problemas no design do banco",
    "show the error in the design of the API",
    "design patterns for the slide component",
    "coloca o deploy em design review",
    # "design" alone, no visual signal
    "o design ficou bom",
    "what do you think about the design?",
    "design",
    # visual words with no design word: a code task, not a design request
    "crie uma landing page em Next.js",
    "corrija o bug na tela de login",
    "add a banner component to the header",
    "faça um post no blog sobre o lançamento",
    "monta os slides em markdown",
    # look-alikes that must not match on a substring
    "migrate postgres to the new server",
    "the designated owner signs off",
    "configure o uiterst flag",
    "",
    "   ",
]


@pytest.mark.parametrize("message", EXPLICIT)
def test_an_explicit_request_opens(message: str) -> None:
    got = detect(message)
    assert got.level == "explicit", (message, got)
    assert got.matched, "an explicit answer names the phrase that decided it"


@pytest.mark.parametrize("message", SUGGEST)
def test_visual_design_suggests(message: str) -> None:
    got = detect(message)
    assert got.level == "suggest", (message, got)
    assert len(got.matched) >= 2, "a suggestion names the design word and the visual signal"


@pytest.mark.parametrize("message", NONE)
def test_code_design_and_plain_requests_change_nothing(message: str) -> None:
    got = detect(message)
    assert got.level == "none", (message, got)
    assert not got.fires


def test_the_table_is_large_enough_to_mean_something() -> None:
    assert len(EXPLICIT) + len(SUGGEST) + len(NONE) >= 60
    assert len(NONE) >= 20, "the negatives are where a keyword detector goes wrong"


def test_folding_removes_accents_and_case() -> None:
    assert fold("  APRESENTAÇÃO   Diseño ") == "apresentacao diseno"


def test_the_answer_serialises_for_the_frame() -> None:
    assert detect("open Chimera Design").to_dict() == {
        "level": "explicit", "reason": "named", "matched": ["chimera design"],
    }
    assert detect("design pattern").to_dict()["level"] == "none"


def test_only_the_start_of_a_huge_message_is_read() -> None:
    tail = " chimera design"
    assert detect("x " * MAX_CHARS + tail).level == "none"
    assert detect(tail + " x" * MAX_CHARS).level == "explicit"


def test_it_is_a_pure_function_that_costs_nothing() -> None:
    """Called on every turn before the agent is built: a thousand calls must not be noticeable."""
    messages = EXPLICIT + SUGGEST + NONE
    first = [detect(m) for m in messages]
    started = time.perf_counter()
    for _ in range(20):
        again = [detect(m) for m in messages]
    elapsed = time.perf_counter() - started
    assert again == first, "same input, same answer"
    # ~1,900 calls. Generous ceiling so a slow CI box stays green; a regression to backtracking
    # patterns would cost seconds, not this.
    assert elapsed < 2.0, f"{len(messages) * 20} detections took {elapsed:.2f}s"


def test_precision_and_recall_on_the_table() -> None:
    expected = {**{m: "explicit" for m in EXPLICIT}, **{m: "suggest" for m in SUGGEST},
                **{m: "none" for m in NONE}}
    got = {m: detect(m).level for m in expected}
    for level in ("explicit", "suggest", "none"):
        predicted = [m for m in expected if got[m] == level]
        actual = [m for m in expected if expected[m] == level]
        hits = [m for m in predicted if expected[m] == level]
        precision = len(hits) / len(predicted) if predicted else 1.0
        recall = len(hits) / len(actual) if actual else 1.0
        assert precision == 1.0 and recall == 1.0, (level, precision, recall)
