"""O portão do redator: só mergeia quando o CI do site aprovou.

Por que isto tem teste próprio. Até esta mudança, `publish()` abria o PR e mandava o merge no mesmo
segundo. A validação de schema do site — `postProblems()` e `articleProblems()`, cobertas pelo vitest
de lá — nunca chegava a rodar antes da publicação. O histórico mostra as duas formas de falhar:
o PR #3 do site teve CI FAILURE e foi mergeado; o #6 saiu CANCELLED, cancelado porque o merge fechou
o PR enquanto o workflow ainda rodava.

O caso que estes testes existem para travar é o mais traiçoeiro: logo depois de o PR nascer, a API
responde `check_runs: []`, porque o workflow ainda não registrou. Ler isso como "nada reprovou,
então pode" reabre o buraco inteiro, e parece pressa em vez de defeito.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SRC = Path(__file__).resolve().parent.parent / "ops" / "chimera_blog_writer.py"
_spec = importlib.util.spec_from_file_location("chimera_blog_writer", _SRC)
assert _spec and _spec.loader
writer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(writer)


def _respostas(monkeypatch: pytest.MonkeyPatch, sequencia: list[Any]) -> list[str]:
    """Faz `gh` devolver cada item da sequência, e registra o que foi pedido."""
    chamadas: list[str] = []
    restante = list(sequencia)

    def fake_gh(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        chamadas.append(f"{method} {path}")
        return restante.pop(0) if restante else (200, {"check_runs": []})

    monkeypatch.setattr(writer, "gh", fake_gh)
    monkeypatch.setattr(writer.time, "sleep", lambda _s: None)
    return chamadas


def test_ausencia_de_check_nunca_e_aprovacao(monkeypatch: pytest.MonkeyPatch) -> None:
    # A API responde vazio enquanto o workflow não registrou. Esperar é a única leitura correta;
    # aprovar seria publicar antes de qualquer validação — o buraco original.
    _respostas(monkeypatch, [(200, {"check_runs": []})] * 200)
    assert writer.checks_pass(1, "abc", timeout_s=0.2) is False


def test_reprova_quando_o_ci_reprovou(monkeypatch: pytest.MonkeyPatch) -> None:
    _respostas(
        monkeypatch,
        [(200, {"check_runs": [{"name": "verify", "status": "completed", "conclusion": "failure"}]})],
    )
    assert writer.checks_pass(3, "abc") is False


def test_cancelado_nao_conta_como_aprovado(monkeypatch: pytest.MonkeyPatch) -> None:
    # Foi o que aconteceu no PR #6: o merge fechou o PR e o workflow morreu no meio. Um check que
    # não terminou não disse nada sobre o conteúdo.
    _respostas(
        monkeypatch,
        [(200, {"check_runs": [{"name": "verify", "status": "completed", "conclusion": "cancelled"}]})],
    )
    assert writer.checks_pass(6, "abc") is False


def test_espera_o_check_em_andamento_antes_de_decidir(monkeypatch: pytest.MonkeyPatch) -> None:
    _respostas(
        monkeypatch,
        [
            (200, {"check_runs": [{"name": "verify", "status": "in_progress", "conclusion": None}]}),
            (200, {"check_runs": [{"name": "verify", "status": "completed", "conclusion": "success"}]}),
        ],
    )
    assert writer.checks_pass(9, "abc") is True


def test_aprova_quando_todos_terminaram_bem(monkeypatch: pytest.MonkeyPatch) -> None:
    # `skipped` e `neutral` são conclusões de sucesso no GitHub — um job condicional que não rodou
    # não é uma reprovação.
    _respostas(
        monkeypatch,
        [
            (
                200,
                {
                    "check_runs": [
                        {"name": "verify", "status": "completed", "conclusion": "success"},
                        {"name": "opcional", "status": "completed", "conclusion": "skipped"},
                    ]
                },
            )
        ],
    )
    assert writer.checks_pass(9, "abc") is True


def test_erro_de_api_nao_vira_aprovacao(monkeypatch: pytest.MonkeyPatch) -> None:
    _respostas(monkeypatch, [(500, {})] * 200)
    assert writer.checks_pass(1, "abc", timeout_s=0.2) is False


def test_publish_nao_mergeia_quando_o_portao_reprova(monkeypatch: pytest.MonkeyPatch) -> None:
    """O teste que prova a ligação, não só a função: `publish` tem de PARAR antes do merge.

    Sem isto, `checks_pass` poderia estar perfeita e não ser chamada — foi assim que dois testes
    meus já passaram sobre código que não estava conectado.
    """
    chamadas: list[str] = []

    def fake_gh(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        chamadas.append(f"{method} {path}")
        if path.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": "base"}}
        if path.endswith("/pulls"):
            return 201, {"number": 42, "head": {"sha": "cafe"}}
        return 200, {}

    monkeypatch.setattr(writer, "gh", fake_gh)
    monkeypatch.setattr(writer, "checks_pass", lambda *_a, **_k: False)

    ok = writer.publish({"content/blog/en/x.md": "corpo"}, "x", "2026-08-11", "analysis")

    assert ok is False
    assert not [c for c in chamadas if c.startswith("PUT") and c.endswith("/merge")]
    # E o branch fica de pé: um boletim reprovado vira um PR esperando alguém, não um rastro apagado.
    assert not [c for c in chamadas if c.startswith("DELETE")]
