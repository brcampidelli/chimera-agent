"""What a pull request ADDS, judged by shape rather than by author.

The repository's security gates all watch one direction. `gitleaks` runs over the full history —
because a secret committed and later deleted is still leaked — and three CVE audits watch what is
installed. Every one of them asks whether something is getting OUT, or whether a dependency is
known-bad.

Nothing asked what a diff brings IN. This repository is public, Apache-2.0, takes pull requests from
anyone, and its own policy is auto-merge except for billing, destructive migrations, RLS and
secrets. A line adding a `curl` to a URL shortener inside `.github/workflows/` or `install.sh`
passes lint, mypy, pytest, gitleaks and all three audits without touching a thing any of them look
at.

**Heuristics, and they are meant to be.** Every rule here has a legitimate use — a shortened link in
a comment, a base64 blob in a fixture, a workflow edit by a maintainer. The output is a finding to
read, not a verdict, and `--fail-on high` is the only level that stops a build. What it buys is that
a human sees the diff line before the merge button does.

Reads `git diff` and stdlib only, so it runs anywhere the checkout does.

    python scripts/pr_intent_scan.py --base origin/main --fail-on high
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

#: Files that describe the patterns this scanner looks for, so scanning them finds every one.
#:
#: Excluded by PATH, never by a marker comment: a marker is something a contributor can copy into
#: the file they are adding, which would turn the exemption into the attack. Two entries, because
#: the tests hold the fixtures — `curl … | bash`, a shortened link, a 200-character blob — and the
#: first real run of this gate flagged its own suite in nine places.
SELF = (
    "scripts/pr_intent_scan.py",
    "tests/test_what_a_pull_request_brings_in.py",
)

#: Paths where an added line runs on somebody else's machine, or on CI, rather than being imported
#: by a test. A change here from an outside contributor is not suspicious by itself — it is the
#: place where every other rule in this file matters more.
SENSITIVE_PATHS = (
    ".github/workflows/",
    ".github/actions/",
    "scripts/",
    "bin/",
    "install",
    "Dockerfile",
    "docker-compose",
    "pyproject.toml",
    "package.json",
)


@dataclass(frozen=True)
class Finding:
    severity: str  # "high" | "medium"
    rule: str
    path: str
    line: str
    why: str


_SHORTENERS = re.compile(
    r"https?://(?:bit\.ly|t\.co|is\.gd|cutt\.ly|tinyurl\.com|goo\.gl|rb\.gy|shorturl\.at|rebrand\.ly)/",
    re.IGNORECASE,
)
_FILE_HOSTS = re.compile(
    r"https?://(?:[\w.-]*\.)?(?:dropbox\.com|drive\.google\.com|mega\.nz|transfer\.sh|"
    r"catbox\.moe|anonfiles\.com|file\.io|pastebin\.com|gist\.githubusercontent\.com)/",
    re.IGNORECASE,
)
_EXECUTABLE_URL = re.compile(
    r"https?://\S+\.(?:sh|ps1|bat|cmd|exe|msi|deb|rpm|dmg|pkg|appimage|tar|tar\.gz|tgz|zip)\b",
    re.IGNORECASE,
)
_LONG_BLOB = re.compile(r"[A-Za-z0-9+/]{120,}={0,2}|[A-Za-z0-9_-]{120,}")
_PIPE_TO_SHELL = re.compile(
    r"\b(?:curl|wget|iwr|Invoke-WebRequest)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|)sh\b", re.IGNORECASE
)


def _added_lines(base: str) -> list[tuple[str, str]]:
    """Every ADDED line in the diff against ``base``, as ``(path, text)``.

    `--unified=0` so context lines are never mistaken for additions — the whole question here is
    what the branch introduces, and a rule that fires on unchanged code would flag every PR that
    happens to touch a file near one.
    """
    diff = subprocess.run(
        ["git", "diff", "--unified=0", f"{base}...HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout
    saida: list[tuple[str, str]] = []
    caminho = ""
    for linha in diff.splitlines():
        if linha.startswith("+++ b/"):
            caminho = linha[6:]
        elif linha.startswith("+") and not linha.startswith("+++"):
            saida.append((caminho, linha[1:]))
    return saida


def scan(base: str) -> list[Finding]:
    achados: list[Finding] = []
    for caminho, texto in _added_lines(base):
        if caminho in SELF:
            continue
        sensivel = any(marca in caminho for marca in SENSITIVE_PATHS)
        grave = "high" if sensivel else "medium"
        if _PIPE_TO_SHELL.search(texto):
            achados.append(Finding(
                "high", "pipe-to-shell", caminho, texto.strip(),
                "a download piped straight into a shell runs whatever the server sends today",
            ))
        if _SHORTENERS.search(texto):
            achados.append(Finding(
                grave, "shortened-url", caminho, texto.strip(),
                "a shortened link hides its destination from the reviewer, and can be repointed later",
            ))
        if _FILE_HOSTS.search(texto):
            achados.append(Finding(
                grave, "external-file-host", caminho, texto.strip(),
                "a file host serves mutable content from outside the repository's supply chain",
            ))
        if _EXECUTABLE_URL.search(texto):
            achados.append(Finding(
                grave, "executable-url", caminho, texto.strip(),
                "a direct link to an executable or archive is a download this project does not pin",
            ))
        if _LONG_BLOB.search(texto) and sensivel:
            achados.append(Finding(
                "high", "encoded-blob", caminho, texto.strip(),
                "a long encoded blob in a path that executes is code a reviewer cannot read",
            ))
    return achados


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="What to diff against.")
    parser.add_argument(
        "--fail-on", default="", choices=["", "high", "medium"],
        help="Exit non-zero at this severity or above. Empty reports without failing.",
    )
    args = parser.parse_args(argv)

    achados = scan(args.base)
    if not achados:
        print(f"pr-intent-scan: nothing to flag in {args.base}...HEAD")
        return 0

    for f in achados:
        print(f"[{f.severity}] {f.rule}  {f.path}")
        print(f"    {f.line[:160]}")
        print(f"    why: {f.why}")

    if not args.fail_on:
        return 0
    limiar = {"high": {"high"}, "medium": {"high", "medium"}}[args.fail_on]
    piores = [f for f in achados if f.severity in limiar]
    if piores:
        print(f"\npr-intent-scan: {len(piores)} finding(s) at or above '{args.fail_on}'")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
