"""The project list says which bookmarks point at a folder that is still there, and which are one folder.

The registry is a bookmark list and a bookmark outlives its folder: a worktree deleted after use, a
repository moved. The Profile screen listed dozens of those, and `chimera-agent` twice because the
same folder was bookmarked under two spellings. The route now answers both questions, so the screen
can filter and merge without guessing from the strings.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.code_projects import folder_exists, folder_key  # noqa: E402
from chimera.interface.session import ChatSession  # noqa: E402


class _NeverRuns:
    def run(self, task: str, **_kw: object) -> object:  # pragma: no cover - defensive
        raise AssertionError("no turn runs in this file")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(
        build_api_app(lambda: ChatSession(_NeverRuns()), settings=settings)  # type: ignore[arg-type]  # never run
    )


def test_a_bookmark_says_whether_its_folder_is_still_on_this_computer(
    client: TestClient, tmp_path: Path
) -> None:
    here = tmp_path / "repo"
    here.mkdir()
    gone = tmp_path / "w-i-deleted-worktree"

    client.post("/api/code/workspaces", json={"path": str(here)})
    client.post("/api/code/workspaces", json={"path": str(gone)})

    by_path = {row["path"]: row for row in client.get("/api/code/workspaces").json()}
    assert by_path[str(here)]["folder_exists"] is True
    assert by_path[str(gone)]["folder_exists"] is False


def test_two_spellings_of_one_folder_share_a_key(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "chimera-agent"
    repo.mkdir()
    # `..` resolves back to the same folder, so the two rows are one project to the person.
    respelled = tmp_path / "other" / ".." / "chimera-agent"
    (tmp_path / "other").mkdir()

    client.post("/api/code/workspaces", json={"path": str(repo)})
    client.post("/api/code/workspaces", json={"path": str(respelled)})

    rows = client.get("/api/code/workspaces").json()
    assert len(rows) == 2
    assert rows[0]["folder_key"] == rows[1]["folder_key"]


def test_different_folders_keep_different_keys(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    assert folder_key(str(a)) != folder_key(str(b))


def test_a_path_that_cannot_be_resolved_is_not_merged_with_anything(tmp_path: Path) -> None:
    # The fallback is the stripped string itself, so two unresolvable rows only merge when they are
    # the same text, never because a resolver failed for both of them.
    assert folder_key("  ") == ""
    assert folder_exists("") is False


def test_the_check_reads_the_disk_each_time(tmp_path: Path) -> None:
    folder = tmp_path / "later"
    assert folder_exists(str(folder)) is False
    folder.mkdir()
    assert folder_exists(str(folder)) is True
