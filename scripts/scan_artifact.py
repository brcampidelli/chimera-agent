"""What is inside the archive we are about to publish.

`publish.yml` already opens the wheel with `zipfile` — and reads only `namelist()`, to confirm one
entry is present. Nothing looks at the CONTENT.

`gitleaks` covers the git history, which is the right scope for the source. It does not cover what
`hatch_build.py` force-includes, nor `apps/desktop/dist` built by an earlier job and downloaded here
as an artifact, nor the PyInstaller binary in the desktop release. Those are assembled after the
last commit and shipped to everyone.

The patterns are the ones :mod:`chimera.core.redact` already carries, reused rather than rewritten:
a second list would drift from the first, and the first is the one with tests.

    python scripts/scan_artifact.py dist/*.whl
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chimera.core.redact import _PATTERNS  # noqa: E402

#: Files whose bytes are worth reading. A wheel is mostly Python and JavaScript; a font or a PNG
#: cannot be reviewed by eye either way, and scanning them is how a scanner earns a reputation for
#: false positives on binary noise.
TEXTO = {".py", ".js", ".mjs", ".cjs", ".ts", ".json", ".toml", ".cfg", ".ini", ".txt", ".md",
         ".yml", ".yaml", ".sh", ".env", ".html", ".css"}

#: Beyond the credential shapes: strings specific to this deployment that must never ship. Named
#: rather than pattern-matched, because a hostname and a state directory have no shape.
NOSSOS = (
    re.compile(r"srv1666151"),
    re.compile(r"hermes_mcp"),
    re.compile(r"/opt/data\b"),
)

#: A wheel legitimately contains this project's own documentation, and this project documents its
#: own redaction patterns. A file whose whole job is to describe a secret's shape is not a leak.
ISENTOS = ("chimera/core/redact.py", "scripts/scan_artifact.py", "scripts/pr_intent_scan.py")


def _members(archive: Path) -> list[tuple[str, bytes]]:
    if archive.suffix in {".whl", ".zip"}:
        with zipfile.ZipFile(archive) as z:
            return [(n, z.read(n)) for n in z.namelist() if Path(n).suffix in TEXTO]
    with tarfile.open(archive) as t:
        saida = []
        for m in t.getmembers():
            if m.isfile() and Path(m.name).suffix in TEXTO:
                f = t.extractfile(m)
                if f is not None:
                    saida.append((m.name, f.read()))
        return saida


def scan(archive: Path) -> list[str]:
    """Every line of ``archive`` that carries something that must not be published."""
    achados: list[str] = []
    for nome, bruto in _members(archive):
        if any(isento in nome.replace("\\", "/") for isento in ISENTOS):
            continue
        try:
            texto = bruto.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for numero, linha in enumerate(texto.splitlines(), 1):
            for padrao in (*_PATTERNS, *NOSSOS):
                if padrao.search(linha):
                    achados.append(f"{nome}:{numero}: {padrao.pattern}")
                    break
    return achados


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args(argv)

    total = 0
    for archive in args.archives:
        achados = scan(archive)
        total += len(achados)
        for a in achados:
            print(f"[leak] {archive.name} :: {a}")
        if not achados:
            print(f"OK: nothing that looks like a credential in {archive.name}")
    return 1 if total else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
