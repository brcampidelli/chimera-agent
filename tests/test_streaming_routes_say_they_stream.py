"""Nove rotas devolviam um stream e o esquema anunciava `application/json` nas nove.

Quem integra pelo contrato — que e' para o que um esquema serve — abre a resposta com um parser de
JSON, recebe `JSONDecodeError` na primeira linha, e conclui que a corrida falhou. Foi exatamente o
que aconteceu ao rodar quatro projetos por esta API: tres modelos apareceram como *"reprovou em
0,2s, $0,00"* antes de terem comecado a trabalhar.

O tipo por si nao gera esquema nenhum: `EventSourceResponse` nao e' um `response_model`, entao o
FastAPI cai no default e ninguem percebe. Estes testes sao a guarda que impede a decima rota de
nascer mentindo — e sao escritos sobre a FONTE, nao sobre o app montado, porque uma rota atras de
um extra que o ambiente de teste nao instalou sumiria do esquema e passaria calada.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVOS = sorted((RAIZ / "chimera/api").glob("*.py"))

#: Os verbos de rota do FastAPI: so' uma funcao decorada com um deles e' rota.
_VERBOS = {"get", "post", "put", "patch", "delete", "api_route"}


def _rotas_que_fazem_stream() -> list[tuple[Path, str, str]]:
    """(arquivo, funcao, decorador) para cada ROTA que devolve um stream.

    Lido pela arvore sintatica, com os decoradores DA PROPRIA funcao. A versao anterior procurava
    uma linha com ``@`` ate seis linhas acima da assinatura e aceitava o decorador de OUTRA funcao:
    `code_api._start_turn`, um auxiliar sem decorador nenhum, "passava" porque o ``@app.post`` da
    rota logo acima estava a cinco linhas. No dia em que um auxiliar novo entrou no meio (a fila
    duravel, 2026-10-16), os dois auxiliares viraram "rotas que mentem". Um auxiliar nao tem
    esquema; a rota que o chama tem, e e' ela que esta guarda confere.
    """
    import ast

    achadas = []
    for arquivo in ARQUIVOS:
        fonte = arquivo.read_text(encoding="utf-8", errors="replace")
        for no in ast.walk(ast.parse(fonte)):
            if not isinstance(no, ast.AsyncFunctionDef) or no.returns is None:
                continue
            if ast.unparse(no.returns) != "EventSourceResponse":
                continue
            rotas = [
                d for d in no.decorator_list
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in _VERBOS
            ]
            if not rotas:
                continue  # auxiliar: devolve o stream de uma rota, nao e' uma
            achadas.append((arquivo, no.name, ast.get_source_segment(fonte, rotas[0]) or ""))
    return achadas


def test_existe_pelo_menos_uma_rota_de_stream() -> None:
    """Sem isto o arquivo inteiro passaria vazio no dia em que o padrao de assinatura mudasse —
    uma guarda que nao encontra nada e uma guarda que nao guarda nada."""
    assert len(_rotas_que_fazem_stream()) >= 9


@pytest.mark.parametrize(
    ("arquivo", "funcao", "decorador"),
    _rotas_que_fazem_stream(),
    ids=[f"{a.stem}:{f}" for a, f, _ in _rotas_que_fazem_stream()],
)
def test_toda_rota_de_stream_declara_que_faz_stream(
    arquivo: Path, funcao: str, decorador: str
) -> None:
    """A regra, uma rota por vez, para o erro dizer QUAL rota mente."""
    assert "SSE_RESPONSE" in decorador, (
        f"{arquivo.name}:{funcao} devolve EventSourceResponse e o esquema dira "
        "application/json. Acrescente `responses=SSE_RESPONSE` ao decorador."
    )


def test_o_dicionario_diz_o_que_o_corpo_e() -> None:
    """Declarar o media type e' metade; a outra metade e' dizer o que ler nele. Um cliente que
    descobre `text/event-stream` ainda precisa saber que cada `data:` carrega um objeto JSON."""
    from chimera.api.sse import SSE_RESPONSE

    corpo = SSE_RESPONSE[200]
    assert "text/event-stream" in corpo["content"]
    assert "application/json" not in corpo["content"]
    descricao = corpo["description"].lower()
    assert "event:" in descricao and "data:" in descricao
    # E o aviso que custou tres celulas de um bench: nao tente parsear como JSON.
    assert "json parser" in descricao


def test_o_esquema_publicado_carrega_isso() -> None:
    """A fonte estar certa nao basta — o que o cliente le' e' o dump. Este e' o unico teste aqui
    que monta o app, e ele pula quando o extra de desktop nao esta instalado em vez de mentir."""
    pytest.importorskip("fastapi")
    from typing import cast

    from chimera.api import build_api_app
    from chimera.interface import ChatSession
    from chimera.interface.session import SupportsRun

    esquema = build_api_app(lambda: ChatSession(cast(SupportsRun, None))).openapi()
    fazem_stream = [
        caminho
        for caminho, ops in esquema["paths"].items()
        for op in ops.values()
        if "text/event-stream" in ((op.get("responses") or {}).get("200") or {}).get("content", {})
    ]

    assert "/api/runs" in fazem_stream, "a rota de execucao voltou a anunciar JSON"
    assert len(fazem_stream) >= 9
