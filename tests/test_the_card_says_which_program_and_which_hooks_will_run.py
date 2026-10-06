"""A card about a shell command says which program the name runs and which git hooks will run.

Study 30, S30-30. A person approving ``git commit -m fix`` approved the words; what ran was whatever
``PATH`` (and, on Windows, the working folder) resolved ``git`` to, plus every hook in the
repository's hooks folder — files the agent can write. The sweep laundered an argument class 9/20
through a ``pre-commit`` running ``git add -A``, and PATH shadowing scored 1.00. The card now carries
both facts, read from the file system before anything runs, and the approval record keeps them.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.governance import pending
from chimera.governance.approval import ask_elsewhere
from chimera.governance.exec_facts import HEADER
from chimera.governance.governed_tool import GovernedTool
from chimera.governance.policy import Decision, Verdict
from chimera.tools.shell import RunShellTool


class _ReviewEverything:
    """A kernel that asks about every call — the card is what is under test, not the policy."""

    def evaluate(self, action: str, **_: Any) -> Verdict:
        return Verdict(Decision.REVIEW, "a person decides", rule="test_review")


def _ask(workspace: Path, command: str, **kwargs: Any) -> str:
    """The action the approver is shown for ``command``; the approver says no, so nothing runs."""
    shown: list[str] = []

    def approve(verdict: Verdict, action: str) -> bool:
        shown.append(action)
        return False

    tool = GovernedTool(RunShellTool(workspace=workspace), _ReviewEverything(), approve=approve)  # type: ignore[arg-type]  # a fake kernel: only `evaluate` is called
    tool.run(command=command, **kwargs)
    assert len(shown) == 1, "precondition: the call was asked about"
    return shown[0]


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _repo(root: Path) -> Path:
    (root / ".git" / "hooks").mkdir(parents=True)
    (root / ".git" / "config").write_text("[core]\n\tbare = false\n", encoding="utf-8")
    return root


def _fake_program(folder: Path, name: str, marker: Path) -> Path:
    """A program called ``name`` in ``folder`` that leaves ``marker`` behind if it ever runs."""
    if sys.platform == "win32":
        return _executable(folder / f"{name}.bat", f'@echo ran> "{marker}"\r\n')
    return _executable(folder / name, f'#!/bin/sh\necho ran > "{marker}"\n')


def test_an_approved_git_commit_names_the_pre_commit_hook_that_will_run(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _executable(repo / ".git" / "hooks" / "pre-commit", "#!/bin/sh\ngit add -A\n")
    _executable(repo / ".git" / "hooks" / "pre-push.sample", "#!/bin/sh\n")

    action = _ask(repo, 'git commit -m "fix the typo"')

    assert HEADER in action
    assert "pre-commit" in action and "WILL RUN" in action
    assert "pre-push" not in action, "a .sample is not a hook"


def test_hooks_that_no_verify_skips_are_not_listed_but_the_rest_are(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _executable(repo / ".git" / "hooks" / "pre-commit", "#!/bin/sh\n")
    _executable(repo / ".git" / "hooks" / "post-commit", "#!/bin/sh\n")

    action = _ask(repo, "git commit --no-verify -m x")

    hooks_line = next(line for line in action.splitlines() if "git commit" in line and "hook" in line)
    assert "post-commit" in hooks_line and "pre-commit" not in hooks_line


def test_a_hooks_path_in_the_repository_config_is_followed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / ".git" / "config").write_text("[core]\n\thooksPath = .githooks\n", encoding="utf-8")
    _executable(repo / ".githooks" / "pre-push", "#!/bin/sh\n")

    action = _ask(repo, "git push origin main")

    assert "pre-push" in action and ".githooks" in action


def test_an_empty_hooks_path_on_the_command_is_said_to_switch_hooks_off(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _executable(repo / ".git" / "hooks" / "pre-commit", "#!/bin/sh\n")

    action = _ask(repo, "git -c core.hooksPath= commit -m x")

    assert "switched off" in action and "WILL RUN" not in action


def test_a_program_shadowing_another_on_path_is_named_by_where_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = tmp_path / "shadow"
    marker = tmp_path / "ran.txt"
    fake = _fake_program(shadow, "git", marker)
    monkeypatch.setenv("PATH", f"{shadow}{os.pathsep}{os.environ.get('PATH', '')}")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    action = _ask(workspace, "git status")

    assert str(fake.resolve()) in action
    assert not marker.exists(), "describing the program must never run it"


def test_a_program_the_agent_could_have_written_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    bin_dir = workspace / "bin"
    _fake_program(bin_dir, "deploytool", tmp_path / "ran.txt")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    action = _ask(workspace, "deploytool --prod")

    assert "INSIDE the project folder" in action


def test_a_command_that_sets_path_itself_is_flagged(tmp_path: Path) -> None:
    action = _ask(tmp_path, "PATH=./bin:$PATH git status")

    assert "changes PATH itself" in action


def test_a_command_with_nothing_to_say_is_shown_as_before(tmp_path: Path) -> None:
    action = _ask(tmp_path, "cd sub")

    assert HEADER not in action


def test_the_approval_record_keeps_what_the_card_showed(tmp_path: Path) -> None:
    """The receipt: history.jsonl carries the resolved programs, not only the first 200 characters."""
    repo = _repo(tmp_path / "repo")
    _executable(repo / ".git" / "hooks" / "pre-commit", "#!/bin/sh\n")
    home = tmp_path / "home"
    asked = ask_elsewhere(home, wait_seconds=0.0)
    tool = GovernedTool(RunShellTool(workspace=repo), _ReviewEverything(), approve=asked)  # type: ignore[arg-type]  # a fake kernel: only `evaluate` is called

    tool.run(command=" ".join(["git", "commit", "-m", "x" * 300]))

    line = json.loads((home / "approvals" / pending.HISTORY).read_text(encoding="utf-8").splitlines()[-1])
    assert line["outcome"] == "timeout"
    assert any("pre-commit" in fact for fact in line.get("programs", []))
