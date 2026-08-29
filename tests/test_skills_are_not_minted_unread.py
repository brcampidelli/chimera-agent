"""Duas metades do flywheel que eram decididas lado a lado e nunca comparadas.

Quatro projetos rodados ponta a ponta pelo app — um site, um jogo, um app financeiro e uma area de
trabalho — cunharam **14 skills e usaram 0**. As duas causas sao independentes e as duas custam
dinheiro em toda corrida recorrente:

1. **Cunhar o que o agente nao le.** Ler cartoes e' opt-in porque foi MEDIDO e nao passou o portao
   registrado (`bench/skillcard`: +16,7 pp, IC inclui zero, +300% de tokens). Escrever nunca foi
   acoplado a isso, entao a instalacao padrao paga uma chamada de proposta, uma validacao e um teste
   de fumaca — e no caminho do painel, uma proposta por modelo do painel mais uma sondagem de
   transferencia em nove modelos — para produzir cartoes que o laco nunca recupera.

2. **Cunhar a quarta copia da mesma coisa.** Tres dos 14 diziam a mesma frase com nomes diferentes:
   `build_standalone_html_from_brief`, `brief_to_offline_single_file_page` e
   `offline_single_file_html_from_brief`. Nada olhava a biblioteca antes de escrever nela.
"""

from __future__ import annotations

from typing import Any

from chimera.evolution.auto_evolve import AutoSkillEvolver, _assinatura, _semelhanca


class _Store(dict):
    """O minimo que o evoluidor toca: adicionar, procurar por nome, listar, buscar."""

    def add(self, skill: Any) -> None:
        self[skill.name] = skill

    def names(self) -> list[str]:
        return list(self)

    def get(self, nome: str) -> Any:  # type: ignore[override]
        return dict.get(self, nome)


class _Skill:
    def __init__(self, name: str, description: str = "", trigger: str = "", do: str = "",
                 kind: str = "pattern") -> None:
        self.name, self.description, self.trigger, self.do, self.kind = (
            name, description, trigger, do, kind
        )
        self.prompt_template = "{tarefa}"
        self.provenance, self.status = "clean", "active"

    def to_dict(self) -> dict:
        return {"name": self.name}


class _Evolver:
    def __init__(self, candidato: Any) -> None:
        self.candidato = candidato

    def propose(self, task: str, solution: str) -> Any:
        return self.candidato

    def test_skill(self, skill: Any, test_input: dict, check: Any) -> bool:
        return True


def _auto(candidato: Any, store: _Store) -> AutoSkillEvolver:
    return AutoSkillEvolver(_Evolver(candidato), store, min_recurrences=1)  # type: ignore[arg-type]


# --------------------------------------------------------------------- a assinatura


def test_o_nome_nao_entra_na_comparacao() -> None:
    """O nome e' a parte que menos ajuda: os tres cartoes duplicados sairam com tres nomes
    diferentes e o mesmo conteudo. Comparar nome nao teria pego nenhum dos tres."""
    a = _Skill("build_standalone_html_from_brief", "gerar pagina HTML de arquivo unico offline")
    b = _Skill("offline_single_file_html_from_brief", "gerar pagina HTML de arquivo unico offline")

    assert _assinatura(a) == _assinatura(b)
    assert a.name != b.name


def test_assinatura_curta_nunca_afirma_semelhanca() -> None:
    """Com quatro palavras ou menos nao da' para dizer que dois cartoes sao a mesma coisa, e um
    falso positivo aqui APAGA aprendizado — e' o erro caro dos dois."""
    store = _Store()
    store.add(_Skill("existente", "faz"))
    kept = _auto(_Skill("novo", "faz"), store).maybe_evolve("t", "s", prior_successes=2)

    assert kept is not None, "descartou por semelhanca calculada sobre quase nada"


# --------------------------------------------------------------------- a deduplicacao


def test_o_terceiro_cartao_igual_nao_entra() -> None:
    """O caso medido, com as frases que os projetos produziram de verdade."""
    store = _Store()
    store.add(_Skill(
        "build_standalone_html_from_brief",
        "construir uma pagina HTML de arquivo unico a partir de um brief, offline, sem framework",
        trigger="brief pede pagina estatica",
        do="escrever index.html com CSS embutido e rodar o verificador",
    ))
    candidato = _Skill(
        "offline_single_file_html_from_brief",
        "construir pagina HTML de arquivo unico offline a partir de brief, sem framework nenhum",
        trigger="brief pede pagina estatica",
        do="escrever index.html com o CSS embutido e rodar o verificador",
    )

    kept = _auto(candidato, store).maybe_evolve("t", "s", prior_successes=2)

    assert kept is None, "a terceira copia da mesma skill entrou na biblioteca"
    assert "offline_single_file_html_from_brief" not in store


def test_um_cartao_sobre_outro_assunto_entra() -> None:
    """A guarda tem de recusar copia e deixar passar novidade — senao ela nao deduplica, ela
    congela a biblioteca no primeiro cartao."""
    store = _Store()
    store.add(_Skill("html", "construir pagina HTML de arquivo unico offline a partir de um brief",
                     do="escrever index.html com CSS embutido"))
    candidato = _Skill("fisica", "implementar funcoes puras de projetil, colisao e pontuacao",
                       do="escrever fisica.js como modulo CommonJS sem DOM")

    assert _auto(candidato, store).maybe_evolve("t", "s", prior_successes=2) is not None


def test_um_anti_padrao_nunca_e_duplicata_de_um_padrao() -> None:
    """Um diz "faca assim" e o outro "nao faca assim". Colapsar os dois apagaria metade do par, e
    sobre o MESMO assunto e' exatamente quando eles se parecem mais."""
    # As frases sao QUASE identicas de proposito: sobre o mesmo assunto, um padrao e um
    # anti-padrao se parecem mais do que dois padroes quaisquer. Se elas nao passassem do limiar,
    # este teste passaria por semelhanca baixa e a guarda de `kind` ficaria inerte sem ninguem ver.
    texto = ("rodar o verificador ate imprimir PASS antes de entregar a tarefa "
             "considerada pronta pelo agente")
    padrao = _Skill("entregar_verificado", texto, trigger="tarefa pronta", do=texto, kind="pattern")
    anti = _Skill("unverified_wip_delivery", texto, trigger="tarefa pronta", do=texto,
                  kind="anti_pattern")
    store = _Store()
    store.add(padrao)

    assert _semelhanca(_assinatura(anti), _assinatura(padrao)) >= 0.72, (
        "as duas frases precisam ser semelhantes o bastante para que so' o `kind` as separe"
    )
    assert _auto(anti, store).maybe_evolve("t", "s", prior_successes=2) is not None


def test_a_deduplicacao_vai_para_a_auditoria() -> None:
    """Uma deduplicacao silenciosa e' a mesma classe de silencio que este projeto passa o dia
    consertando: sem a linha, "nao aprendeu nada" e "ja' sabia" sao o mesmo nada no log."""

    class _Audit:
        def __init__(self) -> None:
            self.rows: list[tuple[str, dict]] = []

        def record(self, kind: str, payload: dict) -> None:
            self.rows.append((kind, payload))

    audit = _Audit()
    store = _Store()
    store.add(_Skill("primeiro", "construir pagina HTML de arquivo unico offline a partir de brief",
                     do="escrever index.html com CSS embutido e rodar o verificador"))
    evolver = AutoSkillEvolver(
        _Evolver(_Skill("segundo", "construir pagina HTML de arquivo unico offline a partir do brief",
                        do="escrever index.html com o CSS embutido e rodar o verificador")),
        store, min_recurrences=1, audit=audit,  # type: ignore[arg-type]
    )

    evolver.maybe_evolve("t", "s", prior_successes=2)

    linhas = [p for k, p in audit.rows if k == "skill_dedupe"]
    assert linhas and linhas[0]["same_as"] == "primeiro"


# --------------------------------------------------------------------- o acoplamento


def test_nao_se_cunha_o_que_o_agente_nao_le() -> None:
    """A decisao mora em `build_evolution_context`, onde leitura e escrita eram resolvidas em
    linhas vizinhas sem nunca se olharem."""
    from pathlib import Path

    fonte = (Path(__file__).resolve().parents[1] / "chimera/evolution/context.py").read_text(
        encoding="utf-8"
    )

    assert "mint = use_cards or settings.mint_unreadable_skills" in fonte
    assert "if evolve_skills and mint:" in fonte
    # E a saida tem de existir: uma pessoa ainda le' os cartoes na tela de Conhecimento, entao
    # colecionar de proposito continua possivel.
    assert "CHIMERA_MINT_UNREADABLE_SKILLS" in fonte


def test_a_saida_existe_e_e_desligada_por_padrao() -> None:
    from chimera.config import Settings

    assert Settings().mint_unreadable_skills is False


def test_jaccard_nao_cobra_uma_chamada_de_modelo() -> None:
    """Deduplicar nao pode custar outra chamada paga — seria trocar um desperdicio por outro."""
    assert _semelhanca({"a", "b", "c"}, {"a", "b", "c"}) == 1.0
    assert _semelhanca({"a", "b"}, {"c", "d"}) == 0.0
    assert _semelhanca(set(), {"a"}) == 0.0
