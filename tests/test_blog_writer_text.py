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

CORPO = "Uma tese.\n\n## Primeiro\n\nO que muda, segundo [S1] e também [S2].\n\n## Depois\n\nFim."
ARTIGO = {"title": "O que muda", "summary": "A tese em uma frase.", "body": CORPO}


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


class TestTraducao:
    def test_ingles_passa_direto(self) -> None:
        assert writer.translate(ARTIGO, "en", 2) is ARTIGO

    def test_traduz(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pt = {**ARTIGO, "body": CORPO.replace("Uma tese.", "Uma tese em português.")}
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
