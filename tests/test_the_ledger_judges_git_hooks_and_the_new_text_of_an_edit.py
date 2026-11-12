"""A tainted write is judged on what it writes and where: ``.git/hooks/*`` and ``.git/config`` run code,
and ``edit_file`` carries its payload under ``new``.

Found by an independent review of the SSH file tools (the remote ``edit_file`` is judged by the same
ledger as the local one, by name). Neither had a code suffix, so a poisoned turn could aim at either
without the ledger seeing a self-modifying write.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.governance.ledger import _CONTENT_KEYS, _first, _is_self_executing


@pytest.mark.parametrize(
    "path",
    [".git/hooks/pre-commit", "repo/.git/hooks/post-merge", ".git/config", "a/b/.git/config",
     ".git/modules/x/hooks/pre-push", ".GIT/HOOKS/pre-commit", "repo\.git\hooks\pre-commit"],
)
def test_git_hooks_and_config_are_self_executing(path: str) -> None:
    assert _is_self_executing(path) is True


@pytest.mark.parametrize(
    "path", [".git/HEAD", ".gitignore", ".github/notes.txt", "hooks/pre-commit", "config", "docs/git/config.txt", ".git/hooks", "notes.txt"],
)
def test_other_paths_stay_ordinary(path: str) -> None:
    assert _is_self_executing(path) is False


def test_the_existing_self_executing_files_are_unchanged() -> None:
    for path in ("run.py", "deploy.sh", "ci.yml", "pyproject.toml", ".bashrc", ".env", "Dockerfile", "a.service"):
        assert _is_self_executing(path) is True


def test_edit_file_content_is_read_from_new_and_a_real_content_key_still_wins() -> None:
    assert _first({"path": ".git/config", "old": "a", "new": "payload"}, _CONTENT_KEYS) == "payload"
    assert _first({"path": "x", "content": "c", "new": "n"}, _CONTENT_KEYS) == "c"
    assert _first({"path": "x", "old": "only the anchor"}, _CONTENT_KEYS) == ""  # `old` is not written


# -- edit_batch carries its paths and texts inside `edits`: each item is judged ---------------------------


def _tainted() -> Any:
    from chimera.governance.ledger import TaintLedger

    ledger = TaintLedger()
    ledger.record_fetch("https://evil.test/p", content="page", requested_by="agent")
    return ledger


def test_edit_batch_is_judged_item_by_item() -> None:
    from chimera.governance.ledger import assess_action

    ledger = _tainted()
    clean = {"path": "notes.txt", "old": "a", "new": "b"}
    poisoned = {"path": "deploy.sh", "old": "a", "new": "run https://evil.test/p"}
    got = assess_action("edit_batch", {"edits": [clean, poisoned]}, ledger)
    assert got.escalate and "deploy.sh" in got.reason and "https://evil.test/p" in got.reason
    assert got.action.startswith("edit_batch:")
    # The same payload aimed at an ordinary file, or a batch with nothing tainted, is not a question.
    assert not assess_action("edit_batch", {"edits": [dict(poisoned, path="notes.txt")]}, ledger).escalate
    assert not assess_action("edit_batch", {"edits": [dict(poisoned, new="plain")]}, ledger).escalate
    # A git hook is judged too, and so is the single-file form, unchanged.
    hook = {"path": ".git/hooks/pre-commit", "old": "a", "new": "x https://evil.test/p"}
    assert assess_action("edit_batch", {"edits": [clean, hook]}, ledger).escalate
    assert assess_action("edit_file", poisoned, ledger).escalate
    # Malformed `edits` never raise: the tool refuses them itself.
    for bad in (None, "x", [1, "a"], {}):
        assert not assess_action("edit_batch", {"edits": bad}, ledger).escalate


@pytest.mark.parametrize("path", [".git/modules/sub/config", ".git/modules/a/b/config.worktree", ".git/config.worktree"])
def test_submodule_and_worktree_git_config_are_self_executing(path: str) -> None:
    assert _is_self_executing(path) is True
