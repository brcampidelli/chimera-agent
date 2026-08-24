"""Forgetting a project forgets its CONVERSATIONS, and never its folder.

The sidebar files coding conversations by the workspace each one recorded, and that grouping is the
only thing a "project" is — no other record of one exists. So the sidebar could rename a project and
not remove it, and a user finished with a folder was stuck with it on screen for ever.

Which makes the semantics the whole risk. "Delete the project" has an obvious wrong reading —
delete the folder — and deleting somebody's source code because they tidied a list is not a
behaviour that gets a second chance to be right. The folder is left alone, and the test that says
so is the one that matters most here.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.core.code_session import CodeSessionStore

PROJETO = "C:\\Users\\alguem\\loja"
OUTRO = "C:\\Users\\alguem\\blog"


def _grava(store: CodeSessionStore, session_id: str, workspace: str) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / f"{session_id}.json").write_text(
        json.dumps({
            "session_id": session_id,
            "workspace": workspace,
            "messages": [{"role": "user", "content": "oi"}],
        }),
        encoding="utf-8",
    )


def _store(tmp_path: Path) -> CodeSessionStore:
    store = CodeSessionStore(tmp_path / "code-sessions")
    _grava(store, "a", PROJETO)
    _grava(store, "b", PROJETO)
    _grava(store, "c", OUTRO)
    return store


def test_it_removes_every_conversation_of_that_project(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.delete_project(PROJETO) == 2
    assert [m["workspace"] for m in store.list_meta()] == [OUTRO]


def test_it_leaves_the_folder_on_disk_alone(tmp_path: Path) -> None:
    """The one that would be a catastrophe, so it is asserted rather than assumed.

    The workspace here is a real directory with a real file in it, and both must survive — a delete
    that reached the filesystem would take somebody's source code with a sidebar click.
    """
    pasta = tmp_path / "projeto-de-verdade"
    pasta.mkdir()
    (pasta / "main.py").write_text("print('oi')", encoding="utf-8")
    store = CodeSessionStore(tmp_path / "code-sessions")
    _grava(store, "a", str(pasta))

    assert store.delete_project(str(pasta)) == 1
    assert pasta.is_dir()
    assert (pasta / "main.py").read_text(encoding="utf-8") == "print('oi')"


def test_an_unknown_project_removes_nothing(tmp_path: Path) -> None:
    """Zero is an answer, not a failure — and it must not be reached by deleting everything else."""
    store = _store(tmp_path)

    assert store.delete_project("C:\\nao\\existe") == 0
    assert len(store.list_meta()) == 3


def test_an_empty_workspace_is_not_a_wildcard(tmp_path: Path) -> None:
    """Conversations with no workspace recorded exist, and `""` must not sweep the whole store.

    The route takes the string from a query parameter, so an empty one is a URL away — this is the
    case where a sloppy match deletes every conversation on the machine.
    """
    store = _store(tmp_path)
    _grava(store, "d", "")

    assert store.delete_project("") == 0
    assert store.delete_project("   ") == 0
    assert len(store.list_meta()) == 4


def test_it_matches_the_grouping_the_list_shows(tmp_path: Path) -> None:
    """Exact match, because the sidebar groups on the exact string.

    Two spellings of one folder appear as two rows, and the click has to remove the row it was on.
    A normalising match would delete a row the user was still looking at.
    """
    store = CodeSessionStore(tmp_path / "code-sessions")
    _grava(store, "a", PROJETO)
    _grava(store, "b", PROJETO + "\\")

    assert store.delete_project(PROJETO) == 1
    assert [m["workspace"] for m in store.list_meta()] == [PROJETO + "\\"]
