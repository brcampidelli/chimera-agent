"""O texto do boletim: nosso, nos nove idiomas, sem os restos que o modelo dirige ao operador.

Três defeitos concretos motivaram estes testes, todos observados no que estava publicado:

1. Um `"(Alternatively, if shorter is preferred: …)"` no ar num boletim em inglês. O modelo ofereceu
   uma escolha ao operador, e o operador era um script.
2. Um boletim em inglês cuja linha de resumo estava em português, porque o resumo era a
   concatenação das manchetes e a primeira fonte do dia era brasileira.
3. Um dry-run em que 6 de 6 itens foram descartados por "sem comentário utilizável" — o prompt
   tratava a descrição da fonte como insumo necessário, e descrições de 51 a 204 caracteres não
   davam. Implicação não depende de detalhe.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SRC = Path(__file__).resolve().parent.parent / "ops" / "chimera_blog_digest.py"
_spec = importlib.util.spec_from_file_location("chimera_blog_digest_text", _SRC)
assert _spec and _spec.loader
digest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(digest)

ITEM = {
    "headline": "Meta returns to open models",
    "description": "",  # o caso que zerou o boletim: sem descrição alguma
    "url": "https://the-decoder.com/x",
    "outlet": "The Decoder",
    "published": "2026-08-10",
}


def _modelo(monkeypatch: pytest.MonkeyPatch, resposta: Any) -> None:
    monkeypatch.setattr(digest, "_ask", lambda *_a, **_k: resposta)


class TestArtefatos:
    def test_remove_a_alternativa_oferecida_ao_operador(self) -> None:
        sujo = "Isso muda o custo de rodar agentes. (Alternatively, if shorter is preferred: muda o custo.)"
        assert digest._clean(sujo) == "Isso muda o custo de rodar agentes."

    def test_remove_o_prefixo_de_cortesia(self) -> None:
        assert digest._clean("Here's the comment: baixa a barreira.") == "baixa a barreira."
        assert digest._clean("Comentário: baixa a barreira.") == "baixa a barreira."

    def test_nao_come_uma_frase_que_so_tem_dois_pontos(self) -> None:
        # O risco do conserto: uma regra ampla demais para prefixos apagaria texto de verdade.
        bom = "O ponto é simples: isso baixa a barreira para quem constrói."
        assert digest._clean(bom) == bom
        outro = "Claro que isso muda o custo de rodar agentes em produção."
        assert digest._clean(outro) == outro

    def test_nao_mutila_um_parenteses_legitimo(self) -> None:
        # A regra caça uma oferta ao operador, não qualquer parêntese — um aposto no fim da frase
        # é texto do comentário.
        bom = "Isso muda o custo (e o risco) de rodar agentes."
        assert digest._clean(bom) == bom

    def test_desembrulha_aspas_e_junta_linhas(self) -> None:
        assert digest._clean('"linha um\nlinha dois"') == "linha um linha dois"


class TestComentarios:
    def test_uma_manchete_sem_descricao_ainda_rende_comentario(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # O gargalo medido no dry-run: 6 de 6 descartados por descrição magra.
        _modelo(monkeypatch, {lang: f"texto {lang}" for lang in digest.LANGS})
        out = digest.comments_for(ITEM)
        assert set(out) == set(digest.LANGS)
        assert out["ja"] == "texto ja"

    def test_um_idioma_faltando_descarta_o_item_inteiro(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Um boletim com buraco em quatro línguas é pior que um item a menos.
        parcial = {lang: f"texto {lang}" for lang in digest.LANGS}
        del parcial["pl"]
        _modelo(monkeypatch, parcial)
        assert digest.comments_for(ITEM) == {}

    def test_skip_do_modelo_e_respeitado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _modelo(monkeypatch, {"skip": True})
        assert digest.comments_for(ITEM) == {}

    def test_falha_do_modelo_nao_levanta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _modelo(monkeypatch, None)
        assert digest.comments_for(ITEM) == {}


class TestResumo:
    def test_o_resumo_sai_nos_nove_idiomas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _modelo(monkeypatch, {lang: f"resumo {lang}" for lang in digest.LANGS})
        out = digest.summaries_for([ITEM])
        assert set(out) == set(digest.LANGS)

    def test_resumo_incompleto_e_descartado_inteiro(self, monkeypatch: pytest.MonkeyPatch) -> None:
        parcial = {lang: f"resumo {lang}" for lang in digest.LANGS}
        parcial["zh"] = ""
        _modelo(monkeypatch, parcial)
        assert digest.summaries_for([ITEM]) == {}


class TestRender:
    def test_usa_o_nosso_resumo_quando_existe(self) -> None:
        item = {**ITEM, **{f"comment_{lang}": f"c-{lang}" for lang in digest.LANGS}}
        md = digest.render("de", "2026-08-11", "fim-do-dia", [item], "", {"de": "Unsere Zeile"})
        assert "summary: Unsere Zeile" in md or "Unsere Zeile" in md
        assert "Bulletin — 2026-08-11, Tagesende" in md
        assert "c-de" in md

    def test_cai_para_as_manchetes_quando_o_resumo_falhou(self) -> None:
        # A reserva é a versão antiga, com a mistura de idiomas que ela sempre teve — e só aparece
        # quando a chamada falhou, o que o log diz.
        item = {**ITEM, **{f"comment_{lang}": f"c-{lang}" for lang in digest.LANGS}}
        md = digest.render("en", "2026-08-11", "meio-dia", [item], "", {})
        assert ITEM["headline"] in md
        assert "Digest — 2026-08-11, midday" in md

    def test_cada_idioma_tem_titulo_e_rotulo_proprios(self) -> None:
        item = {**ITEM, **{f"comment_{lang}": f"c-{lang}" for lang in digest.LANGS}}
        for lang in digest.LANGS:
            md = digest.render(lang, "2026-08-11", "meio-dia", [item], "", {})
            assert digest.TITLE_WORD[lang] in md
            assert digest.SLOT_LABEL["meio-dia"][lang] in md
