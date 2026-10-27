"""A network or device path in the registry is never looked at, and an expanduser failure is not a 500.

On Windows, `resolve()`, `is_dir()`, `exists()` or `read_text()` on `\\\\server\\share\\…` opens a
connection to that server, and the account's NTLM hash goes with it, on every read of the list. The
path is text the app stored, so it may name anything.

The guard below records every call on such a path before refusing it. The assertions read the record,
not just the outcome: a call wrapped in `except Exception` would otherwise pass unseen.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.code_projects import (  # noqa: E402
    GONE,
    HERE,
    UNVERIFIABLE,
    CodeProjectRegistry,
    FolderPresence,
    folder_exists,
    folder_facts,
    folder_key,
    folder_state,
)
from chimera.interface.session import ChatSession  # noqa: E402

UNC = "\\\\fileserver\\share\\repo"
DEVICE = "\\\\?\\UNC\\fileserver\\share\\repo"


class _NeverRuns:
    def run(self, task: str, **_kw: object) -> object:  # pragma: no cover - defensive
        raise AssertionError("no turn runs in this file")


def _is_network(text: str) -> bool:
    return text.startswith("\\\\") or text.startswith("//")


@pytest.fixture
def touched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every disk call on a network path, recorded, and refused."""
    calls: list[str] = []
    real = {
        name: getattr(pathlib.Path, name)
        for name in ("resolve", "is_dir", "exists", "read_text", "stat", "expanduser")
    }

    def make(name: str) -> Any:
        def wrapper(self: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
            if _is_network(str(self)):
                calls.append(f"{name}:{self}")
                raise AssertionError(f"{name} reached a network path: {self}")
            return real[name](self, *args, **kwargs)

        return wrapper

    for name in real:
        monkeypatch.setattr(pathlib.Path, name, make(name))
    return calls


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api import build_api_app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    app = build_api_app(lambda: ChatSession(_NeverRuns()), settings=settings)  # type: ignore[arg-type]  # never run
    yield TestClient(app)
    get_settings.cache_clear()


def test_the_guard_really_intercepts_a_network_path(touched: list[str]) -> None:
    """Without this the tests below could pass because the guard is inert, not because nothing ran."""
    with pytest.raises(AssertionError):
        Path(UNC).is_dir()
    assert touched == [f"is_dir:{UNC}"]


@pytest.mark.parametrize("text", [UNC, DEVICE])
def test_folder_facts_of_a_network_path_resolve_and_stat_nothing(touched: list[str], text: str) -> None:
    key, state = folder_facts(text)

    assert state == UNVERIFIABLE
    assert key == os.path.normcase(text.strip())
    assert touched == []


def test_folder_key_and_exists_of_a_network_path_touch_nothing(touched: list[str]) -> None:
    assert folder_key(UNC) == os.path.normcase(UNC.strip())
    assert folder_state(UNC) == UNVERIFIABLE
    # Not verified means not claimed: the list must not show it as present.
    assert folder_exists(UNC) is False
    assert touched == []


def test_removing_a_network_bookmark_resolves_nothing(touched: list[str], tmp_path: Path) -> None:
    registry = CodeProjectRegistry(tmp_path / "projects.json")
    registry.path.write_text(json.dumps({"projects": [{"path": UNC, "alias": ""}]}), encoding="utf-8")

    rows = registry.remove(UNC)

    assert rows == []
    assert touched == []


def test_registering_a_network_path_is_refused(tmp_path: Path) -> None:
    registry = CodeProjectRegistry(tmp_path / "projects.json")
    with pytest.raises(ValueError, match="network or device"):
        registry.register(UNC)
    assert not registry.path.exists()


def test_the_route_refuses_a_network_path_with_400(client: TestClient) -> None:
    response = client.post("/api/code/workspaces", json={"path": UNC})
    assert response.status_code == 400


def test_listing_a_network_bookmark_does_not_touch_its_folder(
    client: TestClient, touched: list[str], tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "code_projects.json").write_text(
        json.dumps({"projects": [{"path": UNC, "alias": "rede"}]}), encoding="utf-8"
    )

    (row,) = client.get("/api/code/workspaces").json()

    assert row["folder_state"] == "unverifiable"
    assert row["folder_exists"] is False
    assert touched == []


def test_a_cron_job_on_a_network_folder_is_unverifiable_not_missing(
    client: TestClient, touched: list[str], tmp_path: Path
) -> None:
    from chimera.scheduler import CronJob, CronStore

    CronStore(tmp_path / "home" / "scheduler" / "jobs.json").add(
        CronJob(id="net", name="rede", schedule="0 9 * * *", action="x", workspace=UNC)
    )

    (job,) = client.get("/api/cron").json()

    assert job["workspace_unverifiable"] is True
    assert job["workspace_missing"] is False
    assert touched == []


def test_a_project_on_a_network_spec_is_listed_without_reading_the_spec(
    client: TestClient, touched: list[str], tmp_path: Path
) -> None:
    from chimera.orchestration.project import ProjectOrchestrator, ProjectState

    state = ProjectState(id="net1", spec_path=UNC + "\\spec.yaml", workspace=str(tmp_path),
                         board_path=str(tmp_path / "board.json"))
    state.save(ProjectOrchestrator.project_dir(tmp_path / "home", "net1") / "project.json")

    (row,) = client.get("/api/projects").json()

    assert row["title"] == ""
    assert touched == []


def test_an_expanduser_failure_does_not_take_the_whole_cron_list_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`~usuario` that does not exist raises RuntimeError from expanduser on POSIX. One such job used
    to fail `GET /api/cron` for every job. It now counts as a folder that is gone."""
    from chimera.scheduler import CronJob, CronStore

    real = pathlib.Path.expanduser

    def raising(self: pathlib.Path) -> pathlib.Path:
        if "usuarionaoexiste" in str(self):
            raise RuntimeError("Could not determine home directory.")
        return real(self)

    monkeypatch.setattr(pathlib.Path, "expanduser", raising)
    CronStore(tmp_path / "home" / "scheduler" / "jobs.json").add(
        CronJob(id="bad", name="ruim", schedule="0 9 * * *", action="x", workspace="~usuarionaoexiste/site")
    )
    CronStore(tmp_path / "home" / "scheduler" / "jobs.json").add(
        CronJob(id="ok", name="ok", schedule="0 9 * * *", action="x")
    )

    response = client.get("/api/cron")

    assert response.status_code == 200
    by_id = {j["id"]: j for j in response.json()}
    assert by_id["bad"]["workspace_missing"] is True
    assert by_id["ok"]["workspace_missing"] is False


def test_folder_facts_resolves_a_local_path_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    folder = tmp_path / "site"
    folder.mkdir()
    real = pathlib.Path.resolve
    calls: list[str] = []

    def counting(self: pathlib.Path, *args: Any, **kwargs: Any) -> pathlib.Path:
        calls.append(str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "resolve", counting)

    key, state = folder_facts(str(folder))

    assert state == HERE
    assert key
    assert len(calls) == 1


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_presence_is_remembered_for_the_ttl_then_looked_at_again(tmp_path: Path) -> None:
    clock = _Clock()
    presence = FolderPresence(ttl=10.0, clock=clock)
    folder = tmp_path / "w-i-worktree"
    folder.mkdir()

    assert presence.state(str(folder), folder) == HERE
    folder.rmdir()

    clock.now += 9.0
    assert presence.state(str(folder), folder) == HERE  # still inside the window: no disk look

    clock.now += 2.0
    assert presence.state(str(folder), folder) == GONE  # past it: the disk answers again


def test_presence_forgets_nothing_it_has_not_been_asked_for(tmp_path: Path) -> None:
    clock = _Clock()
    presence = FolderPresence(ttl=10.0, clock=clock)
    folder = tmp_path / "missing"

    assert presence.state(str(folder), folder) == GONE
    folder.mkdir()
    # Within the window the earlier answer stands, so a list read twice in a second is one look.
    assert presence.state(str(folder), folder) == GONE

