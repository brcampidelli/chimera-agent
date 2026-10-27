"""The project list names a project by its spec's name, and falls back to the id when it cannot.

The Tasks screen listed projects as bare ids (`84feb3f3`), so the person had to remember which spec
each id was. The spec's name is what the project already says about itself, and the list now carries
it. An unreadable spec must not take the whole list down with it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.interface.session import ChatSession  # noqa: E402
from chimera.orchestration.project import ProjectOrchestrator, ProjectState  # noqa: E402


class _NeverRuns:
    def run(self, task: str, **_kw: object) -> object:  # pragma: no cover - defensive
        raise AssertionError("no turn runs in this file")


SPEC = """
name: Loja de café — catálogo
requirements:
  - id: r1
    text: the catalogue page exists
    check: contains
    target: index.html
    required: true
"""


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def client(home: Path) -> TestClient:
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(home))
    return TestClient(
        build_api_app(lambda: ChatSession(_NeverRuns()), settings=settings)  # type: ignore[arg-type]  # never run
    )


def _project(home: Path, project_id: str, spec_path: Path) -> None:
    state = ProjectState(
        id=project_id,
        spec_path=str(spec_path),
        workspace=str(home),
        board_path=str(home / "board.json"),
    )
    state.save(ProjectOrchestrator.project_dir(home, project_id) / "project.json")


def test_a_project_is_listed_under_its_spec_name(client: TestClient, home: Path, tmp_path: Path) -> None:
    spec = tmp_path / "spec.yaml"
    spec.write_text(SPEC, encoding="utf-8")
    _project(home, "84feb3f3", spec)

    (row,) = client.get("/api/projects").json()

    assert row["id"] == "84feb3f3"
    assert row["title"] == "Loja de café — catálogo"


def test_an_unreadable_spec_leaves_the_title_empty_and_the_list_whole(
    client: TestClient, home: Path, tmp_path: Path
) -> None:
    gone = tmp_path / "deleted-spec.yaml"  # never written
    _project(home, "aaaa1111", gone)
    spec = tmp_path / "ok.yaml"
    spec.write_text(SPEC, encoding="utf-8")
    _project(home, "bbbb2222", spec)

    rows = {row["id"]: row for row in client.get("/api/projects").json()}

    assert rows["aaaa1111"]["title"] == ""
    assert rows["bbbb2222"]["title"] == "Loja de café — catálogo"
