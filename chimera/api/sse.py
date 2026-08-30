"""O que uma rota de streaming devolve, dito no esquema em vez de descoberto no cliente.

Nove rotas devolviam `EventSourceResponse` e o esquema anunciava `application/json` nas nove. Quem
integra pelo contrato — que e' para o que um esquema serve — abre a resposta com um parser de JSON,
recebe `JSONDecodeError` na primeira linha e conclui que a corrida falhou sem gastar nada. Foi
exatamente isso que aconteceu ao rodar quatro projetos por esta API: tres modelos apareceram como
"reprovou em 0,2s, $0,00" antes de terem comecado.

O tipo por si nao gera esquema: `EventSourceResponse` nao e' um `response_model`, entao o FastAPI
cai no default. Este dicionario e' o que corrige, e vai no decorador de toda rota que faz stream.
"""

from __future__ import annotations

from typing import Any

#: Anexado como ``responses=SSE_RESPONSE`` em toda rota que devolve ``EventSourceResponse``.
#:
#: O corpo e' um fluxo ``text/event-stream``: linhas ``event:`` e ``data:``, com o ``data`` de cada
#: quadro sendo um objeto JSON. Nao ha um schema por quadro aqui de proposito — os tipos de evento
#: diferem por rota, e um schema que listasse os de uma rota so' envelheceria calado nas outras.
SSE_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "Server-sent events. Each frame is an `event:` line plus a `data:` line holding a JSON "
            "object; the event names depend on the route. NOT application/json — reading the body "
            "with a JSON parser fails on the first line."
        ),
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    }
}
