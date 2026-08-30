"""Reverter estava certo. Jogar fora a unica copia do trabalho e' que nao estava.

Medido numa corrida real de um projeto de verdade — uma area de trabalho no navegador, quatro
modelos, portao executavel. Um dos bracos gastou **374 mil tokens e $0,70** em tres tentativas e as
tres foram revertidas. O verificador reprovou nas tres, entao a reversao esta' correta e este
arquivo nao a discute.

O que ele discute e' o que sobrou. Na terceira tentativa o verificador imprimiu

    PASS - nucleo ok
    FAIL (1):
      - janelas sem role=dialog

O nucleo passava a suite inteira — pureza, ordenacao, z-order — e a pagina errava UM atributo. Tudo
foi apagado, e a unica copia que restou foi o patch dentro do recibo, cortado em 4.000 caracteres
sobre um `index.html` de 581 linhas. Um souvenir, nao uma recuperacao.

Os dois requisitos — log limitado e trabalho recuperavel — so' convivem em arquivos diferentes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.autonomous import Attempt, AutonomousAgent


class _Diff:
    def __init__(self, path: str, patch: str) -> None:
        self.path, self.patch, self.truncated = path, patch, False


def _agente(tmp_path: Path) -> Any:
    """So' os campos que `_preserve_discarded` toca — o resto do agente nao participa disto."""
    a = AutonomousAgent.__new__(AutonomousAgent)
    a.run_log = tmp_path / "runs.jsonl"
    return a


def _tentativa(*diffs: _Diff, run_id: str = "abc123") -> Attempt:
    t = Attempt(index=3, answer="", approved=False, verified=False, reverted=False)
    t.diffs = list(diffs)  # type: ignore[assignment]
    t.run_id = run_id
    return t


def test_o_arquivo_grande_sobrevive_inteiro(tmp_path: Path) -> None:
    """O caso medido: 581 linhas. O corte de 4.000 caracteres do recibo nao pode alcancar isto."""
    grande = "\n".join(f"+  <div class='janela-{i}'>conteudo</div>" for i in range(581))
    t = _tentativa(_Diff("index.html", grande))

    caminho = _agente(tmp_path)._preserve_discarded(t, 3)

    assert caminho, "nada foi guardado"
    texto = Path(caminho).read_text(encoding="utf-8")
    assert len(texto) > 4000, "guardou uma copia cortada, que e' o problema e nao a solucao"
    assert "janela-580" in texto, "a ultima linha do arquivo nao sobreviveu"


def test_guarda_todos_os_arquivos_da_tentativa(tmp_path: Path) -> None:
    """A tentativa que valia a pena recuperar tinha DOIS arquivos, e o que passava a suite era o
    segundo. Guardar so' o primeiro seria perder exatamente a parte boa."""
    t = _tentativa(_Diff("index.html", "+<html>"), _Diff("nucleo.js", "+module.exports = {}"))

    texto = Path(_agente(tmp_path)._preserve_discarded(t, 3)).read_text(encoding="utf-8")

    assert "index.html" in texto and "nucleo.js" in texto
    assert "module.exports" in texto


def test_diz_por_que_aquilo_esta_ali(tmp_path: Path) -> None:
    """Um arquivo de diff solto numa pasta, sem cabecalho, e' lixo que ninguem sabe apagar nem
    usar. Ele tem de dizer que aquilo foi revertido e por que."""
    texto = Path(
        _agente(tmp_path)._preserve_discarded(_tentativa(_Diff("a.js", "+x")), 3)
    ).read_text(encoding="utf-8")

    assert "revertida" in texto
    assert "verificador reprovou" in texto


def test_sem_diff_nao_cria_arquivo_vazio(tmp_path: Path) -> None:
    """Uma tentativa que nao escreveu nada nao tem trabalho a preservar, e uma pasta cheia de
    arquivos vazios ensina a ignorar a pasta."""
    t = Attempt(index=1, answer="", approved=False, verified=False, reverted=False)
    t.run_id = "x"

    assert _agente(tmp_path)._preserve_discarded(t, 1) == ""
    assert not (tmp_path / "discarded").exists()


def test_guardar_nunca_impede_reverter(tmp_path: Path, monkeypatch: Any) -> None:
    """Esta e' a ordem que importa: preservar acontece ANTES de restaurar, e restaurar e' o que
    mantem o workspace consistente. Um erro ao guardar tem de devolver "" e sair do caminho."""
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))

    assert _agente(tmp_path)._preserve_discarded(_tentativa(_Diff("a", "+x")), 1) == ""


def test_a_preservacao_vem_antes_da_restauracao() -> None:
    """Afirmado sobre a fonte porque a ordem e' o invariante: depois de `restore` o disco ja' nao
    tem o que copiar, e um teste de comportamento passaria com as duas linhas trocadas."""
    fonte = (
        Path(__file__).resolve().parents[1] / "chimera/core/autonomous.py"
    ).read_text(encoding="utf-8")
    preservar = fonte.index("attempt.discarded_at = self._preserve_discarded(attempt, index)")
    restaurar = fonte.index("self.guard.restore(snapshot)", preservar - 400)

    assert preservar < restaurar, "guardar passou a acontecer depois de apagar"


def test_o_recibo_leva_o_caminho() -> None:
    """Guardar sem dizer onde e' guardar num lugar que ninguem encontra."""
    from chimera.api.runs import AttemptReceipt

    assert "discarded_at" in AttemptReceipt.model_fields
    fonte = (
        Path(__file__).resolve().parents[1] / "chimera/api/runs.py"
    ).read_text(encoding="utf-8")
    assert 'discarded_at=str(getattr(a, "discarded_at", "") or "")' in fonte
