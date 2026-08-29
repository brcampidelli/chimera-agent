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

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVOS = sorted((RAIZ / "chimera/api").glob("*.py"))

#: `async def nome(...) -> EventSourceResponse:` — a assinatura que promete um stream.
_STREAM = re.compile(r"async def (\w+)\([^)]*\)\s*->\s*EventSourceResponse\s*:", re.S)


def _rotas_que_fazem_stream() -> list[tuple[Path, str, str]]:
    """(arquivo, funcao, decorador) para cada rota que devolve um stream."""
    achadas = []
    for arquivo in ARQUIVOS:
        fonte = arquivo.read_text(encoding="utf-8", errors="replace")
        linhas = fonte.splitlines()
        for m in _STREAM.finditer(fonte):
            linha = fonte[: m.start()].count("\n")
            # O decorador e' a ultima linha comecando com @ acima da assinatura.
            decorador = next(
                (linhas[i] for i in range(linha, max(-1, linha - 6), -1)
                 if linhas[i].lstrip().startswith("@")),
                "",
            )
            achadas.append((arquivo, m.group(1), decorador))
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
