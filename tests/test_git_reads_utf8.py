"""Git output is UTF-8. Decoding it with the machine's locale loses whole repositories.

`_git` ran `subprocess.run(..., text=True)` with no `encoding=`, so Python decoded git's stdout
with the locale codec. On Windows that is cp1252, where the bytes `0x81 0x8D 0x8F 0x90 0x9D` are
undefined — and those bytes appear in the UTF-8 encoding of an emoji, of most CJK, and of a great
deal of ordinary content.

What happens then is worse than an error. The `UnicodeDecodeError` is raised on the reader thread,
`CompletedProcess.stdout` comes back as `None`, and the caller sees a successful process with no
output. `GET /api/git/diff` then fed `None` into a response model whose field is `str` and returned
**500 with a plain-text body**, so a client calling `.json()` on it broke a second time.

Measured on rc14: reproducible 7/7 against the app's own installation directory, whose JavaScript
bundles contain those bytes. A two-line repository whose only change is adding an emoji reproduces
it, while `git/status` on that same repository answers 200 — because status prints file names and
diff prints content.

`errors="replace"` rather than strict: a diff viewer that refuses to show a file because one byte
in it is not valid UTF-8 is less useful than one that shows the byte as a replacement character.
Git's own output is UTF-8 on every platform, so this is the correct codec, not a guess.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chimera.core.worktree import _git, is_git_repo


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "a.txt").write_text("linha um\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return root


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("emoji", "linha um\numa cobra: \U0001F40D\n"),
        ("cjk", "linha um\n你好世界\n"),
        # Inside cp1252, so this one passed even before the fix. Kept so a regression that
        # narrowed the codec instead of widening it would still be caught.
        ("acentos", "linha um\ncoração, maçã, único\n"),
    ],
)
def test_a_diff_survives_content_the_locale_cannot_decode(
    tmp_path: Path, label: str, content: str
) -> None:
    root = _repo(tmp_path)
    (root / "a.txt").write_text(content, encoding="utf-8")

    result = _git(["diff"], root)

    assert result.returncode == 0
    assert result.stdout is not None, f"{label}: stdout came back None — the decode threw"
    assert "a.txt" in result.stdout


def test_the_text_itself_arrives_not_just_a_non_empty_string(tmp_path: Path) -> None:
    # `errors="replace"` must not be reached for content that IS valid UTF-8. Without this, a fix
    # that decoded as latin-1 would pass every assertion above while mangling every accent.
    root = _repo(tmp_path)
    (root / "a.txt").write_text("linha um\numa cobra: \U0001F40D\n", encoding="utf-8")

    out = _git(["diff"], root).stdout

    assert "\U0001F40D" in out


def test_file_names_survive_too(tmp_path: Path) -> None:
    # `git status --porcelain` prints paths, and a path is as free to hold an emoji as a line is.
    root = _repo(tmp_path)
    (root / "relatório \U0001F40D.txt").write_text("x\n", encoding="utf-8")

    out = _git(["status", "--porcelain"], root).stdout

    assert out is not None
    assert "relat" in out


def test_a_repo_check_still_works(tmp_path: Path) -> None:
    # The cheapest caller of `_git`, and the one everything else is gated on. Named separately
    # because a change to the wrapper that broke this would look like "not a git repository"
    # everywhere rather than like an error.
    assert is_git_repo(_repo(tmp_path)) is True
    assert is_git_repo(tmp_path / "nowhere") is False
