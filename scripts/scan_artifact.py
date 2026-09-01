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

#: A last-resort path list, for scanning an artifact with no repository beside it. When the
#: repository IS available, `_do_ultimo_commit` below subsumes this: these three are committed
#: source like any other. A file whose whole job is to describe a secret's shape is not a leak.
ISENTOS = ("chimera/core/redact.py", "scripts/scan_artifact.py", "scripts/pr_intent_scan.py")


def _do_ultimo_commit(nome: str, bruto: bytes, repo: Path | None) -> bool:
    """Whether this member is byte-identical to a file in the checked-out repository.

    The scope this module claims in its own docstring is *what is assembled after the last commit*.
    Committed source is covered by `gitleaks`, which runs on the same tree with `fetch-depth: 0` and
    reads the history as well. Scanning it here a second time buys nothing and costs the gate its
    credibility: on the first release it ever guarded, it stopped the publish on twenty-nine hits,
    every one of them a fake key inside a test whose subject IS what a key looks like. You cannot
    assert that `sk-…` is masked without writing something that matches `sk-…`.

    The alternative was to keep extending `ISENTOS` — eight test files that day, more the next. That
    is a path allowlist, and a path allowlist is exactly the hole you do not want in the files most
    likely to hold a real key one day.

    This is narrower than it looks, and that is the point: the comparison is on BYTES, so a member
    at a path that exists in the repository but with different content is still read. To get a
    credential past this you would have to commit it, and `gitleaks` fails the build when you do.
    With no repository to compare against, nothing is skipped.
    """
    if repo is None:
        return False
    caminho = nome.replace("\\", "/")
    # An sdist wraps everything in `<name>-<version>/`; a wheel's members are already repo-relative.
    candidatos = [caminho, caminho.partition("/")[2]]
    for c in candidatos:
        if not c:
            continue
        arquivo = repo / c
        try:
            if arquivo.is_file() and arquivo.read_bytes() == bruto:
                return True
        except OSError:
            continue
    return False


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


def scan(archive: Path, repo: Path | None = None) -> list[str]:
    """Every line of ``archive`` that carries something that must not be published.

    ``repo`` is the checked-out tree the artifact was built from. Members identical to a file in it
    came from the last commit and are `gitleaks`' scope, not this one; pass ``None`` to read
    everything, which is what happens when the artifact is scanned on its own.
    """
    achados: list[str] = []
    for nome, bruto in _members(archive):
        if any(isento in nome.replace("\\", "/") for isento in ISENTOS):
            continue
        if _do_ultimo_commit(nome, bruto, repo):
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
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="the checked-out tree this was built from; members identical to a file in it are "
        "`gitleaks`' scope, not this one. Point it at a directory that does not exist to read "
        "everything.",
    )
    args = parser.parse_args(argv)

    repo = args.repo if args.repo and args.repo.is_dir() else None
    if repo is None:
        print("note: no repository to compare against — reading every member", file=sys.stderr)

    total = 0
    for archive in args.archives:
        achados = scan(archive, repo)
        total += len(achados)
        for a in achados:
            print(f"[leak] {archive.name} :: {a}")
        if not achados:
            print(f"OK: nothing that looks like a credential in {archive.name}")
    return 1 if total else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
