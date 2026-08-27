"""The ``defines`` check has to recognise a definition in the languages this project writes.

Measured before the fix, against twenty real definition forms:

    python def · python class                    2 matched
    everything else — JS, TS, Rust, Go           0 matched

So on any non-Python project a ``defines`` requirement could never be satisfied. The project would
write the code, be told it had not finished, and loop to its iteration ceiling before reporting
failure over work that was correct. Fail-closed, and from the outside indistinguishable from an
agent that cannot code.

**The control is the half that matters.** Widening a positive check is the direction that breaks
things: a requirement satisfied by a mention is worse than one that is never satisfied, because it
reports done. Ten near-misses are pinned below and none of them may ever match.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.governance.drift import Spec, check_drift

#: Every one of these defines `saudacao`.
DEFINE = [
    ("python def", "def saudacao():\n    pass\n"),
    ("python async def", "async def saudacao():\n    pass\n"),
    ("python class", "class saudacao:\n    pass\n"),
    ("python indentado", "class A:\n    def saudacao(self):\n        pass\n"),
    ("js function", "function saudacao() {}\n"),
    ("js export function", "export function saudacao() {}\n"),
    ("js async function", "async function saudacao() {}\n"),
    ("js export default", "export default function saudacao() {}\n"),
    ("js generator", "function* saudacao() {}\n"),
    ("js class", "class saudacao extends Base {}\n"),
    ("arrow const", "const saudacao = () => {};\n"),
    ("arrow export", "export const saudacao = (a, b) => a + b;\n"),
    ("arrow um argumento", "const saudacao = x => x;\n"),
    ("arrow async", "const saudacao = async () => {};\n"),
    ("ts anotado", "const saudacao: Handler = () => {};\n"),
    ("function expression", "const saudacao = function () {};\n"),
    ("rust fn", "fn saudacao() {}\n"),
    ("rust pub fn", "pub fn saudacao(a: u8) {}\n"),
    ("go func", "func saudacao() {}\n"),
    ("go metodo", "func (s *Store) saudacao() {}\n"),
]

#: None of these define it. A `defines` requirement satisfied by any of them reports a project
#: finished on the strength of a call, a comment, or a variable holding a number.
NAO_DEFINE = [
    ("uma chamada", "saudacao()\n"),
    ("um import python", "from x import saudacao\n"),
    ("um import js", 'import { saudacao } from "./x";\n'),
    ("um re-export", 'export { saudacao } from "./x";\n'),
    ("um comentario", "# saudacao faz o cumprimento\n"),
    ("uma string", 'print("saudacao")\n'),
    ("um numero", "const saudacao = 5;\n"),
    ("um texto", 'const saudacao = "oi";\n'),
    ("um apelido", "saudacao = outra\n"),
    ("uma propriedade", "const cfg = {\n  saudacao: true,\n};\n"),
]


def _spec() -> Spec:
    return Spec.model_validate(
        {
            "name": "x",
            "requirements": [
                {"id": "tem-saudacao", "check": "defines", "target": "saudacao", "required": True}
            ],
        }
    )


@pytest.mark.parametrize(("rotulo", "codigo"), DEFINE, ids=[r for r, _ in DEFINE])
def test_reconhece_a_definicao(rotulo: str, codigo: str, tmp_path: Path) -> None:
    (tmp_path / "arquivo.txt").write_text(codigo, encoding="utf-8")
    assert check_drift(_spec(), tmp_path).aligned, f"{rotulo} nao foi reconhecido"


@pytest.mark.parametrize(("rotulo", "codigo"), NAO_DEFINE, ids=[r for r, _ in NAO_DEFINE])
def test_nao_confunde_uma_mencao_com_uma_definicao(rotulo: str, codigo: str, tmp_path: Path) -> None:
    (tmp_path / "arquivo.txt").write_text(codigo, encoding="utf-8")
    assert not check_drift(_spec(), tmp_path).aligned, f"{rotulo} passou por definicao"


def test_um_nome_parecido_nao_serve(tmp_path: Path) -> None:
    """`\\b` at the end, still. `saudacaoAntiga` is a different function, and a check that accepted
    it would report a requirement met by code that does not exist."""
    (tmp_path / "a.js").write_text("export function saudacaoAntiga() {}\n", encoding="utf-8")
    assert not check_drift(_spec(), tmp_path).aligned


def test_um_nome_com_caracteres_de_regex_nao_explode(tmp_path: Path) -> None:
    """The target is written by a model or by hand and lands inside a pattern. `re.escape` is what
    stops `Pedido.total` from matching `Pedidoxtotal` — and stops an unbalanced paren from raising
    mid-project, where a verdict belongs."""
    (tmp_path / "a.py").write_text("def Pedido_total():\n    pass\n", encoding="utf-8")
    spec = Spec.model_validate(
        {
            "name": "x",
            "requirements": [
                {"id": "r", "check": "defines", "target": "Pedido.total", "required": True}
            ],
        }
    )
    assert not check_drift(spec, tmp_path).aligned
