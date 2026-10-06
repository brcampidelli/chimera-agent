"""The card never says "no hook will run" where git reads the hooks folder differently than it did.

Second review of the S30-30 card (study 30). With git 2.53, in five configurations the agent can
set up, git RAN an executable ``pre-commit`` while the card said ``git commit: no repository hook
will run``:

1. ``hooksPath = <dir> ; note`` — git drops the comment; the reader kept ``<dir> ; note``, an empty
   folder.
2. ``[core] hooksPath = <dir>`` on one line — the reader took the whole line as the section name.
3. ``git -c include.path=<file> commit`` — ``<file>`` set the folder; ``-c include.*`` was skipped.
4. ``git --config-env=core.hooksPath=HP commit`` — skipped as a generic flag.
5. ``git --git-dir=<other>/.git --work-tree=. commit`` — skipped; the cwd repository was read.

Each case below builds a real repository and first runs real git, to show the hook does run — a
test of the card against a configuration git ignores would prove nothing. The card must then say
the hooks could not be determined.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from chimera.governance import exec_facts
from chimera.governance.exec_facts import describe

NO_HOOK = "no repository hook will run"
UNSURE = "could NOT be determined"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs a real git")


@pytest.fixture(autouse=True)
def _no_outside_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's own ~/.gitconfig, system config and GIT_* variables are not under test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(exec_facts, "_system_configs", lambda: [])
    for name in exec_facts._GIT_ENV:
        monkeypatch.delenv(name, raising=False)


def _git_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid", **extra)
    return env


def _init(root: Path) -> Path:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=_git_env())
    return root


def _hook(folder: Path) -> Path:
    """An executable pre-commit that leaves a mark where git runs it (the worktree's top)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "pre-commit"
    path.write_text("#!/bin/sh\necho ran > hook-ran.txt\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _git_runs_the_hook(command: list[str], cwd: Path, mark_in: Path, **env: str) -> None:
    """Precondition: real git, given exactly these words, runs the hook."""
    subprocess.run(command, cwd=cwd, check=True, env=_git_env(**env), capture_output=True)
    assert (mark_in / "hook-ran.txt").is_file(), "precondition: git did not run the hook"


def _append(config: Path, text: str) -> None:
    with config.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _card(command: str, cwd: Path) -> str:
    return "\n".join(describe(command, cwd, cwd))


COMMIT = ["commit", "--allow-empty", "-q", "-m", "x"]


def test_a_comment_after_the_hooks_folder_is_not_read_as_part_of_it(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    evil = tmp_path / "evil" / "hooks"
    _hook(evil)
    _append(repo / ".git" / "config", f"[core]\n\thooksPath = {evil.as_posix()} ; note\n")
    _git_runs_the_hook(["git", *COMMIT], repo, repo)

    text = _card("git commit -m x", repo)

    assert NO_HOOK not in text, text
    assert UNSURE in text, text


def test_a_key_on_the_section_line_is_not_missed(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    evil = tmp_path / "evil" / "hooks"
    _hook(evil)
    _append(repo / ".git" / "config", f"[core] hooksPath = {evil.as_posix()}\n")
    _git_runs_the_hook(["git", *COMMIT], repo, repo)

    text = _card("git commit -m x", repo)

    assert NO_HOOK not in text, text
    assert UNSURE in text, text


def test_a_config_file_included_on_the_command_line_is_not_skipped(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    evil = tmp_path / "evil" / "hooks"
    _hook(evil)
    inc = tmp_path / "inc.cfg"
    inc.write_text(f"[core]\n\thooksPath = {evil.as_posix()}\n", encoding="utf-8")
    words = ["git", "-c", f"include.path={inc.as_posix()}", *COMMIT]
    _git_runs_the_hook(words, repo, repo)

    text = _card(f"git -c include.path={inc.as_posix()} commit -m x", repo)

    assert NO_HOOK not in text, text
    assert UNSURE in text, text


def test_a_hooks_folder_set_from_an_environment_variable_is_not_skipped(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    evil = tmp_path / "evil" / "hooks"
    _hook(evil)
    words = ["git", "--config-env=core.hooksPath=HP", *COMMIT]
    _git_runs_the_hook(words, repo, repo, HP=evil.as_posix())

    text = _card("git --config-env=core.hooksPath=HP commit -m x", repo)

    assert NO_HOOK not in text, text
    assert UNSURE in text, text


def test_another_repository_named_with_git_dir_is_not_replaced_by_the_cwd_one(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    other = _init(tmp_path / "other")
    _hook(other / ".git" / "hooks")
    words = ["git", f"--git-dir={(other / '.git').as_posix()}", "--work-tree=.", *COMMIT]
    _git_runs_the_hook(words, repo, repo)

    for command in (
        f"git --git-dir={(other / '.git').as_posix()} --work-tree=. commit -m x",
        f"git --git-dir {(other / '.git').as_posix()} --work-tree . commit -m x",
    ):
        text = _card(command, repo)

        assert NO_HOOK not in text, (command, text)
        assert UNSURE in text, (command, text)


def test_a_value_continued_onto_the_next_line_is_said_not_guessed(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    _append(repo / ".git" / "config", "[alias]\n\tst = status \\\n\t-s\n")

    text = _card("git commit -m x", repo)

    assert UNSURE in text, text


def test_a_plain_hooks_folder_is_still_read(tmp_path: Path) -> None:
    """The fail-closed reader does not swallow the ordinary case: a bare or quoted folder is named."""
    repo = _init(tmp_path / "repo")
    evil = tmp_path / "evil" / "hooks"
    _hook(evil)
    _append(repo / ".git" / "config", f'[core]\n\thooksPath = "{evil.as_posix()}"  \n')
    _git_runs_the_hook(["git", *COMMIT], repo, repo)

    text = _card("git commit -m x", repo)

    assert "WILL RUN" in text and "pre-commit" in text, text
