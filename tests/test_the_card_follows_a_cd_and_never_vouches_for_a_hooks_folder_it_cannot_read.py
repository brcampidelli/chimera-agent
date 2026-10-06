"""The card follows a ``cd`` and never says "no hook will run" about a folder it cannot read.

Review of the S30-30 card (study 30). Two ways it gave a POSITIVE false assurance:

- ``cd sub && git commit -m x`` was described from the folder the call started in: the card named
  the system git and said "no repository hook will run (…\\ws\\.git\\hooks)", while cmd.exe ran
  ``sub\\git.bat`` (it looks in the new folder first) and git ran ``sub``'s ``pre-commit`` — exactly
  the laundering case S30-30 is about. A plain ``cd`` is now followed; one that cannot be followed is
  said, and nothing after it is vouched for.
- ``core.hooksPath`` was read from three files only. A repository config with
  ``[include] path = ../x.cfg``, where ``x.cfg`` set ``hooksPath = evil-hooks``, made the card name
  ``.git/hooks`` while git ran ``evil-hooks/pre-commit``. A plain include is now followed; an
  ``includeIf`` that could set the folder, and the ``GIT_CONFIG_*`` / ``GIT_DIR`` variables, make
  the card say the hooks could not be determined.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.governance import exec_facts
from chimera.governance.exec_facts import describe
from chimera.governance.governed_tool import GovernedTool
from chimera.governance.policy import Decision, Verdict
from chimera.tools.shell import RunShellTool

NO_HOOK = "no repository hook will run"
UNSURE = "could NOT be determined"


@pytest.fixture(autouse=True)
def _no_outside_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's own ~/.gitconfig, system config and GIT_* variables are not under test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(exec_facts, "_system_configs", lambda: [])
    for name in exec_facts._GIT_ENV:
        monkeypatch.delenv(name, raising=False)


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _repo(root: Path, config: str = "[core]\n\tbare = false\n") -> Path:
    (root / ".git" / "hooks").mkdir(parents=True)
    (root / ".git" / "config").write_text(config, encoding="utf-8")
    return root


def _workspace_with_a_hooked_sub_repo(tmp_path: Path) -> tuple[Path, Path]:
    ws = _repo(tmp_path / "ws")
    sub = _repo(ws / "sub")
    _executable(sub / ".git" / "hooks" / "pre-commit", "#!/bin/sh\ngit add -A\n")
    return ws, sub


# ------------------------------------------------------------------ cd


def test_cd_into_a_repository_with_a_hook_names_that_repositorys_hook(tmp_path: Path) -> None:
    ws, sub = _workspace_with_a_hooked_sub_repo(tmp_path)

    lines = describe("cd sub && git commit -m x", ws, ws)
    text = "\n".join(lines)

    assert "pre-commit" in text and "WILL RUN" in text, text
    assert str(sub / ".git" / "hooks") in text
    assert NO_HOOK not in text, "the card vouched for the folder the command left"


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe looks in the current folder first")
def test_cd_into_a_folder_holding_git_bat_names_that_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The default cmd.exe search (current folder first); some hosts switch it off, and then the
    # shell the command runs in does not look there either.
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    ws, sub = _workspace_with_a_hooked_sub_repo(tmp_path)
    (sub / "git.bat").write_text("@echo off\r\n", encoding="utf-8")

    text = "\n".join(describe("cd sub && git commit -m x", ws, ws))

    assert f"git -> {(sub / 'git.bat').resolve()}" in text, text
    assert "INSIDE the project folder" in text


def test_the_wiring_shows_the_followed_folder_on_the_card(tmp_path: Path) -> None:
    ws, _sub = _workspace_with_a_hooked_sub_repo(tmp_path)
    shown: list[str] = []

    class _Review:
        def evaluate(self, action: str, **_: Any) -> Verdict:
            return Verdict(Decision.REVIEW, "a person decides", rule="test_review")

    def approve(_verdict: Verdict, action: str) -> bool:
        shown.append(action)
        return False

    tool = GovernedTool(RunShellTool(workspace=ws), _Review(), approve=approve)  # type: ignore[arg-type]  # a fake kernel: only `evaluate` is called
    tool.run(command="cd sub && git commit -m x")

    assert shown and "pre-commit" in shown[0] and NO_HOOK not in shown[0]


@pytest.mark.parametrize(
    "command",
    ["cd $TARGET && git commit -m x", "cd - && git commit -m x", "cd && git commit -m x",
     "(cd sub && git commit -m x)", "cd sub | cat && git commit -m x",
     "pushd sub && popd && git commit -m x"],
    ids=["variable", "dash", "bare", "subshell", "pipe", "popd"],
)
def test_a_cd_that_cannot_be_followed_is_said_and_nothing_after_it_is_vouched_for(
    tmp_path: Path, command: str
) -> None:
    ws, _sub = _workspace_with_a_hooked_sub_repo(tmp_path)

    text = "\n".join(describe(command, ws, ws))

    assert NO_HOOK not in text, text
    assert UNSURE in text and "changes folder" in text


def test_a_command_without_cd_is_described_as_before(tmp_path: Path) -> None:
    ws = _repo(tmp_path / "ws")

    text = "\n".join(describe("git commit -m x", ws, ws))

    assert NO_HOOK in text and UNSURE not in text


# ------------------------------------------------------------------ the config git reads


def test_a_plain_include_that_sets_the_hooks_folder_is_followed(tmp_path: Path) -> None:
    ws = _repo(tmp_path / "ws", "[core]\n\tbare = false\n[include]\n\tpath = ../x.cfg\n")
    (ws / "x.cfg").write_text("[core]\n\thooksPath = evil-hooks\n", encoding="utf-8")
    _executable(ws / "evil-hooks" / "pre-commit", "#!/bin/sh\ngit add -A\n")

    text = "\n".join(describe("git commit -m x", ws, ws))

    assert "WILL RUN" in text and "pre-commit" in text and "evil-hooks" in text, text
    assert NO_HOOK not in text


def test_an_include_if_that_could_set_the_hooks_folder_makes_it_undetermined(tmp_path: Path) -> None:
    ws = _repo(
        tmp_path / "ws",
        '[core]\n\tbare = false\n[includeIf "gitdir:~/"]\n\tpath = ../x.cfg\n',
    )
    (ws / "x.cfg").write_text("[core]\n\thooksPath = evil-hooks\n", encoding="utf-8")

    text = "\n".join(describe("git commit -m x", ws, ws))

    assert UNSURE in text and NO_HOOK not in text, text


def test_an_include_if_that_only_sets_a_name_changes_nothing(tmp_path: Path) -> None:
    ws = _repo(
        tmp_path / "ws",
        '[core]\n\tbare = false\n[includeIf "gitdir:~/"]\n\tpath = ../who.cfg\n',
    )
    (ws / "who.cfg").write_text("[user]\n\temail = a@b.c\n", encoding="utf-8")

    text = "\n".join(describe("git commit -m x", ws, ws))

    assert NO_HOOK in text and UNSURE not in text


@pytest.mark.parametrize("where", ["environment", "command"])
def test_git_config_variables_make_the_hooks_folder_undetermined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    ws = _repo(tmp_path / "ws")
    command = "git commit -m x"
    if where == "environment":
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    else:
        command = "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=h " + command

    text = "\n".join(describe(command, ws, ws))

    assert UNSURE in text and NO_HOOK not in text, text


def test_a_worktree_config_is_read_when_git_reads_it(tmp_path: Path) -> None:
    ws = _repo(tmp_path / "ws", "[core]\n\tbare = false\n[extensions]\n\tworktreeConfig = true\n")
    (ws / ".git" / "config.worktree").write_text("[core]\n\thooksPath = wt-hooks\n", encoding="utf-8")
    _executable(ws / "wt-hooks" / "pre-commit", "#!/bin/sh\n")

    text = "\n".join(describe("git commit -m x", ws, ws))

    assert "WILL RUN" in text and "wt-hooks" in text, text
