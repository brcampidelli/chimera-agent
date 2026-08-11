"""O texto do blog: nosso, nos nove idiomas, sem citar o que não leu.

Este arquivo substituiu os testes do boletim quando o formato mudou, e a mudança removeu a defesa
que aqueles testes cobriam. O boletim deixava ao modelo UM campo de 400 caracteres; um artigo lhe
dá o texto inteiro, que é exatamente onde um modelo fluente inventa número, data e citação.

A defesa nova é estrutural, e é o que estes testes travam: **o modelo não escreve URL nenhuma**.
Ele cita as fontes por marcador — `[S1]`, `[S2]` —, e o link é montado a partir da lista já
verificada. Uma fonte inventada não é detectada; ela é inexprimível, porque não existe campo onde
ela caberia. O que sobra para testar aqui é a rodada que ignorou a instrução, e a tradução que
falhou em silêncio: o marcador que sumiu, o idioma que devolveu o inglês de volta.

Três defeitos do formato anterior continuam servindo de referência do que uma rodada faz de errado:
um `"(Alternatively, if shorter is preferred: …)"` publicado, um post em inglês com a linha de
resumo em português, e um dry-run que descartou 6 de 6 itens por uma regra de prompt boa demais.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SRC = Path(__file__).resolve().parent.parent / "ops" / "chimera_blog_writer.py"
_spec = importlib.util.spec_from_file_location("chimera_blog_writer_text", _SRC)
assert _spec and _spec.loader
writer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(writer)

FONTES = [
    {
        "headline": "Meta returns to open models",
        "description": "",  # sem descrição: o caso que zerava o formato antigo
        "url": "https://the-decoder.com/meta-open-models",
        "outlet": "The Decoder",
        "published": "2026-08-10",
    },
    {
        "headline": "OpenAI lança modelo voltado a segurança",
        "description": "Um modelo treinado para tarefas de defesa.",
        "url": "https://techcrunch.com/openai-cyber",
        "outlet": "TechCrunch",
        "published": "2026-08-10",
    },
]

# O artigo-fonte é em INGLÊS, como o de verdade: é dele que saem as oito traduções e o endereço da
# página. A primeira versão deste arquivo usava um corpo em português aqui, e a trava de idioma —
# escrita depois, por causa de uma execução real que produziu justamente isso — reprovou o fixture.
CORPO = "A claim.\n\n## First\n\nWhat shifts, per [S1] and also [S2].\n\n## Then\n\nEnd of it."
ARTIGO = {"title": "What shifts", "summary": "The thesis in one line.", "body": CORPO}


def _modelo(monkeypatch: pytest.MonkeyPatch, resposta: Any) -> None:
    monkeypatch.setattr(writer, "_ask", lambda *_a, **_k: resposta)


# --------------------------------------------------------------------------- o que o modelo devolveu

class TestForma:
    """`shape_problems` não é uma cópia das regras do site — é a checagem da RODADA.

    As regras de formato de um post vivem no `blog.ts`, testadas no vitest de lá. Duplicá-las aqui
    produziria uma cópia correta exatamente uma vez, até o dia em que o schema mudasse. O que se
    verifica aqui é outra coisa: se veio o que se pediu.
    """

    def test_aceita_o_que_foi_pedido(self) -> None:
        assert writer.shape_problems(ARTIGO, 2) == []

    def test_pega_url_escrita_pelo_modelo(self) -> None:
        # A instrução é "nunca escreva URL". Quando ela é ignorada, o link não passou pela lista
        # verificada — e é assim que uma fonte inventada entraria se pudesse entrar.
        art = {**ARTIGO, "body": "Veja em https://inventado.example/estudo."}
        assert "URL escrita pelo modelo" in " ".join(writer.shape_problems(art, 2))

    def test_pega_marcador_para_fonte_que_nao_existe(self) -> None:
        art = {**ARTIGO, "body": "Segundo [S1] e [S7]."}
        assert "fora da lista de fontes: [7]" in " ".join(writer.shape_problems(art, 2))

    def test_pega_texto_que_nao_cita_fonte_alguma(self) -> None:
        art = {**ARTIGO, "body": "Uma opinião solta, sem nada atrás."}
        assert "não cita nenhuma fonte" in " ".join(writer.shape_problems(art, 2))

    def test_pega_campo_vazio(self) -> None:
        assert "campo title vazio" in " ".join(writer.shape_problems({**ARTIGO, "title": "  "}, 2))

    def test_o_teto_de_titulo_e_relativo_na_traducao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # O teto fixo de 90 reprovou um título alemão de 92 caracteres, e o alemão estava certo:
        # a mesma frase corre uns 30% mais longa. O que se barra é título que virou parágrafo.
        en = {**ARTIGO, "title": "A" * 68}
        alemao = {**ARTIGO, "title": "B" * 92, "body": CORPO + "\nZeile"}
        _modelo(monkeypatch, alemao)
        assert writer.translate(en, "de", 2) == alemao

        paragrafo = {**ARTIGO, "title": "C" * 400, "body": CORPO + "\nZeile"}
        _modelo(monkeypatch, paragrafo)
        assert writer.translate(en, "de", 2) is None


class TestMontagemDoLink:
    def test_marcador_vira_link_da_fonte_verificada(self) -> None:
        saida = writer.link_sources(CORPO, FONTES)
        assert "[Meta returns to open models](https://the-decoder.com/meta-open-models)" in saida
        assert "[S1]" not in saida and "[S2]" not in saida

    def test_a_manchete_nao_e_traduzida(self) -> None:
        # O texto do link é a manchete no idioma do veículo, nas nove versões. Traduzi-la entrega
        # ao leitor um título que não existe na página para onde ele vai.
        corpo_pt = "Conforme [S1], o cenário muda."
        assert "Meta returns to open models" in writer.link_sources(corpo_pt, FONTES)


# --------------------------------------------------------------------------- a redação

class TestRedacao:
    def test_escreve_o_artigo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _modelo(monkeypatch, ARTIGO)
        assert writer.write_article(FONTES) == ARTIGO

    def test_pular_nao_publica_nada(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _modelo(monkeypatch, {"skip": True})
        assert writer.write_article(FONTES) is None

    def test_falha_do_modelo_nao_publica_nada(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _modelo(monkeypatch, None)
        assert writer.write_article(FONTES) is None

    def test_recusa_o_artigo_com_url_inventada(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Com marcador válido junto, de propósito. A primeira versão deste teste usava um corpo sem
        # marcador nenhum e passava mesmo com a regra de URL desligada — quem reprovava era o "não
        # cita nenhuma fonte". Um teste que passa pelo motivo errado não protege a regra que diz
        # proteger, e só o revert-e-rode expõe isso.
        corpo = "Segundo [S1], e também em https://inventado.example/x, tudo muda."
        _modelo(monkeypatch, {**ARTIGO, "body": corpo})
        assert writer.write_article(FONTES) is None


class TestIdiomaFonteENumeros:
    """As duas travas que a primeira execução real pediu."""

    def test_pega_o_artigo_fonte_escrito_em_portugues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Aconteceu: o prompt está em português e o modelo devolveu o artigo inteiro em português
        # para o arquivo `blog/en/`, com o endereço da página em português e as oito traduções
        # saindo de um "original" que não era o idioma-fonte. Nada quebrou; ficou só errado.
        pt = (
            "A indústria de IA está em um ponto onde tamanho não significa desempenho, e para quem "
            "constrói agentes com modelos menores isso muda mais do que parece. Segundo [S1]."
        )
        _modelo(monkeypatch, {**ARTIGO, "body": pt})
        assert writer.write_article(FONTES) is None

    def test_deixa_passar_o_ingles(self) -> None:
        assert writer.looks_english("What a security model changes for people who build agents")

    def test_pega_numero_que_nao_esta_no_material(self) -> None:
        material = "Nemotron 3.5 Lightning, 3.6 billion active parameters, 670 tokens per second."
        corpo = "It runs 3.6 billion parameters and is 40 percent cheaper."
        assert writer.invented_numbers(corpo, material) == ["40"]

    def test_o_texto_de_release_tem_as_mesmas_travas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # As notas serem nossas reduz o risco de inventar sobre terceiros, não o de inventar. Um
        # número de desempenho que ninguém mediu é pior vindo de nós: quem lê tem motivo para crer.
        art = {**ARTIGO, "body": "It ships [S1] and runs 40 percent faster than before."}
        _modelo(monkeypatch, art)
        assert writer.write_update("0.42.0", "Fixes seven settings that would not apply.") is None

    def test_nao_reclama_de_separador_diferente(self) -> None:
        # `3.6` vira `3,6` conforme quem escreve, e uma regra que confunde vírgula com invenção é
        # uma regra que reprova o texto certo.
        assert writer.invented_numbers("são 3,6 bilhões", "3.6 billion active parameters") == []


class TestTraducao:
    def test_ingles_passa_direto(self) -> None:
        assert writer.translate(ARTIGO, "en", 2) is ARTIGO

    def test_traduz(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pt = {**ARTIGO, "body": CORPO.replace("A claim.", "Uma tese em português.")}
        _modelo(monkeypatch, pt)
        assert writer.translate(ARTIGO, "pt", 2) == pt

    def test_pega_marcador_perdido_na_traducao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Uma fonte que some do texto naquele idioma some em silêncio: a página continua inteira
        # sem ela, e só o alemão fica sem saber de onde veio a afirmação.
        _modelo(monkeypatch, {**ARTIGO, "body": "Was sich ändert, laut [S1]."})
        assert writer.translate(ARTIGO, "de", 2) is None

    def test_pega_o_ingles_devolvido_como_traducao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _modelo(monkeypatch, dict(ARTIGO))
        assert writer.translate(ARTIGO, "ja", 2) is None

    def test_uma_chamada_instavel_nao_custa_o_dia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Aconteceu na primeira execução real: o modelo devolveu o inglês para o português e a
        # rodada morreu ali, levando junto os nove idiomas. A mesma chamada, repetida, traduziu.
        boa = {**ARTIGO, "body": CORPO.replace("A claim.", "Una tesis.")}
        respostas = [dict(ARTIGO), boa]
        monkeypatch.setattr(writer, "_ask", lambda *_a, **_k: respostas.pop(0))
        assert writer.translate(ARTIGO, "es", 2) == boa

    def test_mas_duas_falhas_seguidas_cancelam(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A retentativa é para instabilidade, não para insistir até o modelo acertar por acaso.
        chamadas = []
        monkeypatch.setattr(writer, "_ask", lambda *a, **k: chamadas.append(1) or dict(ARTIGO))
        assert writer.translate(ARTIGO, "fr", 2) is None
        assert len(chamadas) == 2


class TestLimiteDeTaxa:
    """Nove chamadas seguidas encontram o limite de taxa lá pela sétima."""

    def _http(self, monkeypatch: pytest.MonkeyPatch, codigos: list[int]) -> list[float]:
        esperas: list[float] = []
        monkeypatch.setattr(writer.time, "sleep", lambda s: esperas.append(s))
        monkeypatch.setattr(writer, "env", lambda k: "chave" if "KEY" in k else None)

        def urlopen(*_a: Any, **_k: Any) -> Any:
            codigo = codigos.pop(0)
            if codigo != 200:
                raise writer.urllib.error.HTTPError("u", codigo, "x", {}, None)  # type: ignore[arg-type]

            class Resp:
                def __enter__(self) -> Any:
                    return self

                def __exit__(self, *_e: Any) -> None:
                    return None

                def read(self) -> bytes:
                    return b'{"choices":[{"message":{"content":"{\\"ok\\":1}"}}]}'

            return Resp()

        monkeypatch.setattr(writer.json, "load", lambda r: writer.json.loads(r.read()))
        monkeypatch.setattr(writer.urllib.request, "urlopen", urlopen)
        return esperas

    def test_espera_e_cresce_no_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        esperas = self._http(monkeypatch, [429, 429, 200])
        assert writer._ask("p", 100) == {"ok": 1}
        assert esperas == [20, 45]  # repetir sem esperar não é uma tentativa

    def test_desiste_depois_de_tres_esperas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._http(monkeypatch, [429, 429, 429, 429])
        assert writer._ask("p", 100) is None

    def test_outro_erro_http_nao_vira_espera(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Um 401 repetido quatro vezes com pausas é uma rodada de três minutos para descobrir que
        # a chave está errada.
        esperas = self._http(monkeypatch, [401])
        assert writer._ask("p", 100) is None
        assert esperas == []


class TestComposicao:
    def test_nove_idiomas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        traduzido = {**ARTIGO, "body": CORPO + "\n\nlinha extra"}
        _modelo(monkeypatch, traduzido)
        files = writer.compose(ARTIGO, FONTES, "2026-08-12", "o-que-muda", "analysis", "", "")
        assert files is not None
        assert len(files) == len(writer.LANGS) == 9
        assert set(files) == {f"content/blog/{lang}/o-que-muda.md" for lang in writer.LANGS}

    def test_um_idioma_que_falha_cancela_a_rodada(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Nove ou nenhum. Cinco idiomas publicados e quatro perdidos deixam um site que parece
        # deliberado, e quem lê não tem como saber que houve falha.
        _modelo(monkeypatch, None)
        assert writer.compose(ARTIGO, FONTES, "2026-08-12", "x", "analysis", "", "") is None


# --------------------------------------------------------------------------- o arquivo

class TestArquivo:
    def _frente(self, texto: str) -> str:
        return texto.split("---")[1]

    def test_analise_declara_as_fontes_em_dados(self) -> None:
        saida = writer.render(ARTIGO, "2026-08-12", "analysis", FONTES, "10 examinadas", "")
        frente = self._frente(saida)
        assert "category: analysis" in frente
        assert "url: https://the-decoder.com/meta-open-models" in frente
        assert 'outlet: "TechCrunch"' in frente
        assert "dropped:" in frente
        # E o corpo já sai com os links montados, sem marcador sobrando.
        assert "[S1]" not in saida

    def test_release_nomeia_a_versao_e_nao_finge_apuracao_externa(self) -> None:
        nota = {
            "headline": "Chimera Agent v0.42.0",
            "url": "https://github.com/brcampidelli/chimera-agent/releases/tag/v0.42.0",
            "outlet": "GitHub",
            "published": "2026-08-12",
        }
        art = {**ARTIGO, "body": "O que mudou, nas notas [S1]."}
        saida = writer.render(art, "2026-08-12", "update", [nota], "", "0.42.0")
        frente = self._frente(saida)
        assert 'version: "0.42.0"' in frente
        # A "fonte" é nossa: vira link no corpo, não vira lista que finge leitura de terceiros.
        assert "sources:" not in frente
        assert "releases/tag/v0.42.0" in saida

    def test_aspas_sempre_no_yaml(self) -> None:
        # Uma manchete traz apóstrofo, dois-pontos e travessão; adivinhar quando aspas são
        # dispensáveis é como se produz um YAML que analisa e diz outra coisa.
        art = {**ARTIGO, "title": 'Um título: com "aspas" e dois-pontos'}
        frente = self._frente(writer.render(art, "2026-08-12", "analysis", FONTES, "", ""))
        assert 'title: "Um título: com \\"aspas\\" e dois-pontos"' in frente


class TestNotasDeRelease:
    def _gh(self, monkeypatch: pytest.MonkeyPatch, resposta: Any, status: int = 200) -> None:
        monkeypatch.setattr(writer, "gh", lambda *_a, **_k: (status, resposta))

    def test_le_a_versao_e_as_notas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._gh(monkeypatch, {"tag_name": "v0.42.0", "body": "x" * 200})
        assert writer.release_notes("0.42.0") == ("0.42.0", "x" * 200)

    def test_recusa_tag_que_o_portao_do_site_reprovaria(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # O site exige `version` no formato x.y.z. Uma tag `v0.42.0-rc1` viraria um post que o CI
        # de lá reprova — melhor não gastar o PR.
        self._gh(monkeypatch, {"tag_name": "v0.42.0-rc1", "body": "x" * 200})
        assert writer.release_notes("0.42.0-rc1") is None

    def test_recusa_release_sem_notas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._gh(monkeypatch, {"tag_name": "v0.42.0", "body": "ver CHANGELOG"})
        assert writer.release_notes("0.42.0") is None
