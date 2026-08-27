"""A spec stored in the folder it judges must not count as evidence for itself.

Every ``contains`` requirement is a regular expression searched across every text file in the
project folder, and each requirement carries a ``text`` field written for a human — which repeats
the very words the regex looks for. Put the spec in the repository, as the project has always
advised, and it satisfies its own checks.

Measured before the fix, with the control alongside it:

    spec inside the folder, nothing written  ->  aligned=True    <- reports done, verified nothing
    spec outside the folder, nothing written ->  aligned=False   <- correct

Both arms agree after the fix. This is not hypothetical for the conversational drafter: writing
the drafted spec into the workspace is the right thing to do — it leaves the acceptance authority
versioned and reviewable — and it is exactly the move that fired the bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.governance.drift import Spec, check_drift, load_spec

YAML = """name: padaria
requirements:
  - id: mostra-o-nome
    text: A pagina deve mostrar o nome da padaria, Padaria Aurora.
    check: contains
    target: Padaria Aurora
    required: true
"""


@pytest.fixture
def spec_no_projeto(tmp_path: Path) -> tuple[Spec, Path]:
    (tmp_path / "projeto.spec.yaml").write_text(YAML, encoding="utf-8")
    return load_spec(tmp_path / "projeto.spec.yaml"), tmp_path


def test_a_spec_sozinha_na_pasta_nao_satisfaz_a_si_mesma(spec_no_projeto) -> None:
    spec, ws = spec_no_projeto
    report = check_drift(spec, ws)
    assert not report.aligned
    assert report.results[0].satisfied is False


def test_o_controle_da_o_mesmo_veredito(tmp_path: Path) -> None:
    """The same spec kept outside the folder. If this arm ever disagreed with the one above, the
    exclusion would be doing something other than what it claims."""
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "projeto.spec.yaml").write_text(YAML, encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    assert not check_drift(load_spec(fora / "projeto.spec.yaml"), ws).aligned


def test_o_codigo_de_verdade_ainda_satisfaz(spec_no_projeto) -> None:
    """The control that matters more than the fix. Excluding one file is only correct if every
    other file still counts — an exclusion that swallowed the workspace would pass the test above
    and make the whole gate useless."""
    spec, ws = spec_no_projeto
    (ws / "index.html").write_text("<h1>Padaria Aurora</h1>", encoding="utf-8")
    assert check_drift(spec, ws).aligned


def test_uma_spec_construida_na_memoria_varre_tudo(tmp_path: Path) -> None:
    """No source, no exclusion. A `Spec(...)` built in code has no file to skip, and skipping
    nothing is the correct behaviour rather than a special case."""
    (tmp_path / "a.txt").write_text("Padaria Aurora", encoding="utf-8")
    spec = Spec.model_validate({"name": "x", "requirements": [
        {"id": "r", "check": "contains", "target": "Padaria Aurora", "required": True},
    ]})
    assert spec.source is None
    assert check_drift(spec, tmp_path).aligned


def test_o_negativo_tambem_ignora_a_spec(tmp_path: Path) -> None:
    """`absent` scans separately from `contains`, and the two must agree about what counts as a
    file. Otherwise a spec that forbids a word would fail on its own requirement text — the same
    bug wearing the opposite sign, and the one that fails CLOSED, so nobody would report it as
    anything but a mysterious refusal to finish.
    """
    (tmp_path / "regra.spec.yaml").write_text(
        "name: sem-segredo\n"
        "requirements:\n"
        "  - id: nada-de-api-key\n"
        "    text: O codigo nao pode conter a palavra API_KEY escrita direto.\n"
        "    check: absent\n"
        "    target: API_KEY\n"
        "    required: true\n",
        encoding="utf-8",
    )
    spec = load_spec(tmp_path / "regra.spec.yaml")
    assert check_drift(spec, tmp_path).aligned

    (tmp_path / "app.py").write_text("API_KEY = 'x'", encoding="utf-8")
    assert not check_drift(spec, tmp_path).aligned


def test_a_origem_nao_vaza_na_serializacao(spec_no_projeto) -> None:
    """It is a local absolute path on somebody's machine. It exists to be excluded from a scan,
    not to be published in an API response or written back into a YAML file."""
    spec, _ = spec_no_projeto
    assert "source" not in spec.model_dump()
