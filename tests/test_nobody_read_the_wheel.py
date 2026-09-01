"""The publish step opened the wheel and read only its file list.

`publish.yml` already does `zipfile.ZipFile(...).namelist()` — to assert one entry is present. The
bytes were never looked at.

`gitleaks` covers the git history, with `fetch-depth: 0`, and that is the right scope for the
source. It does not cover what `hatch_build.py` force-includes, nor `apps/desktop/dist` built by an
earlier job and downloaded here as an artifact, nor the PyInstaller binary in the desktop release —
all assembled AFTER the last commit and shipped to everyone.

This project has paid for that gap twice already: an OpenRouter key in plain text, and a PassaPro
token in cleartext in the ops repository. Neither was in a wheel, and neither needed to be for the
next one to be.

The patterns are the ones `chimera.core.redact` already carries, imported rather than rewritten: a
second list drifts from the first, and the first is the one with tests.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from scripts.scan_artifact import scan


def _wheel(tmp_path: Path, **arquivos: str) -> Path:
    caminho = tmp_path / "pacote-0.1-py3-none-any.whl"
    with zipfile.ZipFile(caminho, "w") as z:
        for nome, conteudo in arquivos.items():
            z.writestr(nome.replace("__", "/"), conteudo)
    return caminho


VAZAMENTOS = [
    ("chimera__x.py", 'CHAVE = "sk-proj-AAAAAAAAAAAAAAAAAAAAAA"\n', "uma chave de API"),
    ("chimera__y.py", 'TOKEN = "ghp_' + "B" * 30 + '"\n', "um token do GitHub"),
    ("chimera__z.json", '{"auth": "Bearer AbCdEfGhIjKlMnOpQrStUvWxYz01"}\n', "um bearer"),
    ("chimera__ops.py", 'HOST = "srv1666151.hstgr.cloud"\n', "o host da VPS"),
    ("chimera__ops2.py", 'CHAVE_SSH = "~/.ssh/hermes_mcp"\n', "o nome da chave SSH"),
    ("_desktop_dist__app.js", 'const k="sk-proj-AAAAAAAAAAAAAAAAAAAAAA";\n', "no bundle do app"),
]


@pytest.mark.parametrize(("nome", "conteudo", "porque"), VAZAMENTOS)
def test_a_secret_in_the_wheel_is_found(tmp_path: Path, nome: str, conteudo: str, porque: str) -> None:
    assert scan(_wheel(tmp_path, **{nome: conteudo})), porque


def test_it_says_which_file_and_which_line(tmp_path: Path) -> None:
    """A gate that says "something leaked" and stops there sends whoever is releasing on a hunt
    through a zip. The release is exactly when nobody has time for that."""
    # Keyword names map `__` to `/`, so a third pair turns the EXTENSION into a directory
    # (`chimera/x/py`), the member is skipped for having no suffix, and the scanner reports
    # nothing — a fixture that quietly proved the opposite of what it was written to prove.
    caminho = _wheel(tmp_path, **{"chimera__x.py": "ok\nok\nCHAVE = 'sk-proj-AAAAAAAAAAAAAAAAAAAAAA'\n"})

    achado = scan(caminho)[0]

    assert "chimera/x.py" in achado
    assert ":3:" in achado


# --------------------------------------------------------------- what must not be flagged


LIMPO = {
    "chimera__core.py": "def somar(a: int, b: int) -> int:\n    return a + b\n",
    "chimera__cfg.py": 'CHAVE = os.environ["OPENROUTER_API_KEY"]\n',
    "chimera__doc.md": "Set `OPENROUTER_API_KEY` in your environment.\n",
    "_desktop_dist__app.js": 'const t=document.getElementById("app");\n',
    "pacote-0.1.dist-info__METADATA": "Name: chimera-agent\nVersion: 0.1\n",
}


def test_an_ordinary_wheel_passes(tmp_path: Path) -> None:
    """A release gate that fails on a clean build is a release gate somebody deletes."""
    assert scan(_wheel(tmp_path, **LIMPO)) == []


def test_the_redaction_module_is_exempt(tmp_path: Path) -> None:
    """A wheel carries this project's own source, and this project's source DESCRIBES the shapes of
    secrets. A file whose whole job is to say what a key looks like is not a key.

    Two things were wrong with this test for its whole life. It passed `chimera__core__redact__py`,
    which `_wheel` turns into `chimera/core/redact/py` — no suffix, so `TEXTO` dropped the member
    before a byte was read and `== []` held without the exemption running. And its payload was
    `redact.py`'s real source, which matches none of its own patterns (`sk|pk|rk)-...` is not
    `sk-...`), so even read, there was nothing to exempt. It asserted that a file with no findings
    has no findings.

    The payload below does match, and the control puts the same bytes at a path that is not exempt.
    """
    payload = 'EXEMPLO = "sk-AAAABBBBCCCCDDDD1234"  # what a key looks like\n'

    assert scan(_wheel(tmp_path, **{"chimera/core/redact.py": payload})) == []
    assert scan(_wheel(tmp_path, **{"chimera/core/outro.py": payload})) != [], (
        "the same bytes under a non-exempt name must be found, or this proves nothing about ISENTOS"
    )


def test_a_binary_member_is_skipped(tmp_path: Path) -> None:
    """Reading a PNG for credential shapes is how a scanner earns a reputation for crying at noise,
    and a scanner nobody believes is one that gets removed."""
    caminho = tmp_path / "p.whl"
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr("chimera/logo.png", bytes(range(256)) * 8)

    assert scan(caminho) == []


def test_it_runs_where_it_is_actually_run(tmp_path: Path) -> None:
    """The gate must work with the standard library alone, because that is all the publish job has.

    Every test above passes in an environment where `chimera` is installed with its runtime
    dependencies. The publish job is not that environment: it installs build tooling, builds the
    wheel, and runs this scanner — and nothing else. On 2026-09-01, the first time the gate ever
    executed, it died on `ModuleNotFoundError: No module named 'rich'` before reading a single byte,
    because `chimera.core.redact` imported `_SECRET_MARKERS` from `chimera.sandbox.local`, which
    reaches `chimera.proc` and then `chimera.telemetry`.

    The shape of that defect is worth naming: **a gate whose only execution path is the irreversible
    operation it guards.** It had five green tests and had never run. This one runs it the way the
    job does — `-S` drops site-packages, so a third-party import fails exactly as it would there.
    """
    import subprocess
    import sys

    raiz = Path(__file__).resolve().parents[1]
    roda = subprocess.run(
        [sys.executable, "-S", str(raiz / "scripts" / "scan_artifact.py"), str(_wheel(tmp_path, **LIMPO))],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(raiz), "PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
    )

    # The control that keeps this honest: without it, `-S` silently keeping site-packages would make
    # the test pass while proving nothing about the environment it claims to reproduce.
    sem_terceiros = subprocess.run(
        [sys.executable, "-S", "-c", "import rich"],
        capture_output=True,
        env={"PYTHONPATH": str(raiz), "PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
    )
    assert sem_terceiros.returncode != 0, "`-S` did not remove third-party packages; this test proves nothing"

    assert roda.returncode == 0, roda.stderr


# ------------------------------------------------------ what the last commit already accounted for


def _repo_falso(tmp_path: Path, arquivos: dict[str, str]) -> Path:
    """A checked-out tree, written as BYTES.

    `write_text` translates `\\n` to `\\r\\n` on Windows while `zipfile.writestr` does not, so the
    two sides of a byte comparison differed by line ending alone and this fixture failed on Windows
    only. The rule under test is content identity; the platform's newline policy is not part of it.
    """
    raiz = tmp_path / "repo"
    for nome, conteudo in arquivos.items():
        destino = raiz / nome
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo.encode("utf-8"))
    return raiz


#: A test whose subject IS the shape of a key. It cannot be written without one.
SOBRE_CHAVES = 'def test_a_key_is_masked():\n    assert redact("sk-AAAABBBBCCCCDDDD1234") == MASK\n'

#: A real suffix. `TEXTO` drops anything else before a byte is read, which is how the exemption test
#: above spent its whole life green without scanning a thing.
ALVO = "tests/test_keys.py"


def test_a_member_that_came_from_the_last_commit_is_not_read(tmp_path: Path) -> None:
    """`gitleaks` runs on the same tree with `fetch-depth: 0`. Reading committed source here a
    second time buys nothing, and on the first release this gate ever guarded it cost the publish:
    twenty-nine hits, every one a fake key inside a test about what a key looks like."""
    repo = _repo_falso(tmp_path, {ALVO: SOBRE_CHAVES})
    wheel = _wheel(tmp_path, **{ALVO: SOBRE_CHAVES})

    assert scan(wheel, repo) == []


def test_the_same_path_with_different_bytes_is_still_read(tmp_path: Path) -> None:
    """The one that decides whether this is a rule or a hole.

    Comparing PATHS would let anything through under a name the repository happens to carry — which
    is what `ISENTOS` does, and why it does not scale past three entries. The comparison is on
    bytes, so a member the build altered is read even where the path is familiar.
    """
    repo = _repo_falso(tmp_path, {ALVO: SOBRE_CHAVES})
    adulterado = SOBRE_CHAVES + '\nTOKEN = "sk-ZZZZYYYYXXXXWWWW9876"\n'
    wheel = _wheel(tmp_path, **{ALVO: adulterado})

    achados = scan(wheel, repo)

    assert len(achados) == 2, achados


def test_with_no_repository_everything_is_read(tmp_path: Path) -> None:
    """Fail closed. A missing checkout must not read as "nothing to see" — that is how a gate ends
    up green because the thing it compares against was not there."""
    wheel = _wheel(tmp_path, **{ALVO: SOBRE_CHAVES})

    assert scan(wheel, None) != []
    assert scan(wheel, tmp_path / "nao-existe") != []
