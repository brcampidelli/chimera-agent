"""Uma corrida que morre no meio gastou dinheiro. Ate agora ela nao deixava nem motivo nem recibo.

Medido numa falha real, rodando um projeto pela API do app: a corrida listou o diretorio, teve um
comando de shell recusado pela governanca, escreveu um modulo de 2.210 caracteres — e terminou como

    event: error
    data: {"message": "the run failed"}

Quatro palavras. Nenhuma causa, nenhuma linha em `runs.jsonl`, e portanto nada na tela de Custo. As
chamadas de modelo que pagaram por aquele modulo ficaram invisiveis, e quem assistia nao tinha como
separar "o provedor caiu" de "a tarefa estava errada".

Isto e' uma classe DIFERENTE do recibo que ja' tem conserto: la' o recibo falhava ao serializar e
cai num registro minimo; aqui a corrida morre antes de chegar naquela linha.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")


class _Attempt:
    index, success = 1, False
    prompt_tokens, completion_tokens = 8123, 977
    usd: float | None = 0.031
    model = "openrouter/qwen/qwen3.8-flash"


class _AgentQueMorre:
    """Um agente que faz trabalho, cobra por ele, e entao estoura — a forma da falha medida."""

    def __init__(self) -> None:
        self.attempts = [_Attempt()]

    def run(self, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("provider returned an unparseable tool call")


def _linhas(home: Path) -> list[dict]:
    log = home / "runs.jsonl"
    if not log.is_file():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_o_recibo_da_corrida_morta_guarda_o_que_ela_gastou(tmp_path: Path) -> None:
    """Os tokens sao a parte que mais nada reconstroi: o workspace sobrevive a uma corrida morta e a
    resposta tambem, mas o que ela CUSTOU so' existe aqui."""
    from chimera.api.app import _persist_crashed_run

    class _Req:
        task = "construa o jogo"

    _persist_crashed_run(
        tmp_path, "run-1", _Req(), tmp_path / "ws", _AgentQueMorre(), RuntimeError("estourou")
    )

    linhas = _linhas(tmp_path)
    assert len(linhas) == 1, "a corrida morta nao deixou linha nenhuma"
    linha = linhas[0]
    assert linha["crashed"] is True, "sem isto ela se le' como uma corrida que so' reprovou"
    assert "RuntimeError" in linha["crash_reason"]
    assert linha["attempts"][0]["prompt_tokens"] == 8123
    assert linha["attempts"][0]["completion_tokens"] == 977
    assert linha["workspace"].endswith("ws"), "sem o workspace a linha nao se junta a nada"


def test_uma_corrida_morta_nao_se_confunde_com_uma_reprovada(tmp_path: Path) -> None:
    """`success: False` sozinho ja' existia e significa "o verificador disse nao". Morrer no meio e'
    outra coisa, e um leitor que nao consegue separar as duas nao consegue diagnosticar nenhuma."""
    from chimera.api.app import _persist_crashed_run

    class _Req:
        task = "t"

    _persist_crashed_run(tmp_path, "r", _Req(), tmp_path, _AgentQueMorre(), ValueError("x"))

    linha = _linhas(tmp_path)[0]
    assert linha["success"] is False
    assert linha.get("crashed") is True


def test_o_relator_nunca_derruba_o_que_ele_relata(tmp_path: Path, monkeypatch: Any) -> None:
    """Isto roda no caminho onde algo ja' deu errado. Um relator de falha que falha nao relata nada
    — e pior, transforma um erro em dois."""
    from chimera.api.app import _persist_crashed_run

    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))

    class _Req:
        task = "t"

    _persist_crashed_run(tmp_path / "x", "r", _Req(), tmp_path, _AgentQueMorre(), ValueError("x"))


def test_um_agente_sem_tentativas_ainda_deixa_a_razao(tmp_path: Path) -> None:
    """A corrida pode morrer antes da primeira tentativa. Sem tokens para relatar, a razao e' o
    unico conteudo — e continua sendo mais do que quatro palavras."""
    from chimera.api.app import _persist_crashed_run

    class _Vazio:
        attempts: list[Any] = []

    class _Req:
        task = "t"

    _persist_crashed_run(tmp_path, "r", _Req(), tmp_path, _Vazio(), TimeoutError("sem resposta"))

    linha = _linhas(tmp_path)[0]
    assert linha["attempts"] == []
    assert "TimeoutError" in linha["crash_reason"]


def test_o_quadro_de_erro_carrega_a_causa() -> None:
    """A metade que o usuario ve'. O tipo da excecao identifica a falha e nao pode carregar segredo;
    a mensagem vai junto e limitada, porque "RuntimeError" sozinho e' pouco melhor que "failed"."""
    fonte = (
        Path(__file__).resolve().parents[1] / "chimera/api/app.py"
    ).read_text(encoding="utf-8")

    assert '"reason": f"{type(exc).__name__}: {exc}"[:400]' in fonte
    assert "_persist_crashed_run(settings.home, run_id, req, ws, auto, exc)" in fonte
