"""The Storage report on worktrees (P1-18): how much they hold, why each still exists, when the total
is too much, and which idle conversations could be ARCHIVED. Nothing here removes a folder.

Measured 2026-11-12 on the owner's machine (`study/auditoria-2026-11/areas/a12-worktrees-medicao.md`):
89 conversation checkouts, 72.9 GiB, in the project's durable folder beside the repository — and the
card listed only the 67 empty ones in temp, because the call that finds worktree folders without a
project never looks there. A card that says "worktrees are small" while they fill the disk is the
failure; these tests pin the three things that fix it: every folder is listed with a size, an age and
a reason; a total over the limit says so; and the offer is the conversation list's archive route and
nothing that removes a folder.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import chimera.core.storage as storage
from chimera.config import Settings, get_settings
from chimera.core.storage import Size, WorktreeOwner, measure, owner_key
from chimera.core.worktree import DURABLE_DIR, find_worktree_dirs


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Temp is the test's own, so nothing on this machine is counted."""
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", "")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browsers"))
    get_settings.cache_clear()
    storage.forget_shared_reports()
    yield temp
    get_settings.cache_clear()
    storage.forget_shared_reports()


def make_worktree(parent: Path, name: str, size: int, *, age_days: float = 3.0) -> Path:
    folder = parent / f"chimera-wt-{name}"
    folder.mkdir(parents=True)
    (folder / "file.bin").write_bytes(b"x" * size)
    then = time.time() - age_days * 86400
    os.utime(folder, (then, then))
    return folder


def rows_by_name(report: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = report["worktrees"]
    assert isinstance(rows, list)
    return {Path(row["path"]).name.removeprefix("chimera-wt-"): row for row in rows}


def three_worktrees(temp: Path) -> tuple[dict[str, Path], dict[str, list[WorktreeOwner]]]:
    """One archived conversation's folder, one that is not archived, and one nobody uses."""
    folders = {
        "archived": make_worktree(temp, "archived", 1000, age_days=40),
        "open": make_worktree(temp, "open", 2000, age_days=20),
        "nobody": make_worktree(temp, "nobody", 3000, age_days=5),
    }
    owners = {
        owner_key(folders["archived"]): [
            WorktreeOwner("s-arch", "old work", True, folder=str(folders["archived"]),
                          due_at=1_900_000_000.0, kept="dirty")
        ],
        owner_key(folders["open"]): [
            WorktreeOwner("s-open", "current work", False, folder=str(folders["open"]), idle_days=20.0)
        ],
    }
    return folders, owners


def test_every_worktree_is_listed_with_its_size_its_age_and_why_it_still_exists(
    tmp_path: Path, _isolated: Path
) -> None:
    folders, owners = three_worktrees(_isolated)

    report = measure(tmp_path / "home", owners=owners)

    rows = rows_by_name(report)
    assert set(rows) == {"archived", "open", "nobody"}, "all three are listed, none dropped"
    assert [rows[k]["bytes"] for k in ("archived", "open", "nobody")] == [1000, 2000, 3000]
    assert rows["archived"]["age_days"] == pytest.approx(40, abs=0.2)
    assert rows["open"]["age_days"] == pytest.approx(20, abs=0.2)
    assert rows["nobody"]["age_days"] == pytest.approx(5, abs=0.2)
    assert rows["archived"]["retention"] == "archived"
    assert rows["archived"]["retention_due_at"] == 1_900_000_000.0
    assert rows["archived"]["retention_kept"] == "dirty", "what the clean-up recorded, not a guess"
    assert (rows["open"]["retention"], rows["open"]["session_id"]) == ("conversation", "s-open")
    assert rows["nobody"]["retention"] == "no_conversation"
    assert all(folder.is_dir() for folder in folders.values()), "reading removes nothing"


def test_a_folder_that_was_not_counted_says_so_and_is_never_zero(
    tmp_path: Path, _isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_worktree(_isolated, "counted", 500)
    slow = make_worktree(_isolated, "slow", 700)
    real = storage.tree_size

    def tree_size(path: Path, **kw: object) -> Size:
        if Path(path) == slow:
            return Size(None, None, "stopped counting at the time limit")
        return real(path, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(storage, "tree_size", tree_size)

    report = measure(tmp_path / "home")

    rows = rows_by_name(report)
    assert rows["slow"]["bytes"] is None
    assert rows["counted"]["bytes"] == 500
    assert report["worktrees_unmeasured"] == 1
    assert report["worktrees_counted_bytes"] == 500


def test_a_total_above_the_limit_says_so_and_a_total_below_it_says_not(
    tmp_path: Path, _isolated: Path
) -> None:
    three_worktrees(_isolated)  # 6000 bytes in all
    gib = 1024**3

    over = measure(tmp_path / "home", limit_gb=5000 / gib)
    under = measure(tmp_path / "home", limit_gb=7000 / gib)

    assert over["worktrees_over_limit"] is True
    assert under["worktrees_over_limit"] is False
    assert over["worktrees_limit_gb"] == pytest.approx(5000 / gib)


def test_with_a_folder_uncounted_over_is_still_certain_but_under_is_not(
    tmp_path: Path, _isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What WAS counted can only grow, so passing the limit is certain; staying under it is certain
    only when every folder was counted, and the report says null rather than a "no" it cannot back."""
    make_worktree(_isolated, "counted", 4000)
    slow = make_worktree(_isolated, "slow", 100)
    real = storage.tree_size
    monkeypatch.setattr(
        storage,
        "tree_size",
        lambda path, **kw: Size(None, None, "slow") if Path(path) == slow else real(path, **kw),
    )
    gib = 1024**3

    assert measure(tmp_path / "home", limit_gb=3000 / gib)["worktrees_over_limit"] is True
    assert measure(tmp_path / "home", limit_gb=9000 / gib)["worktrees_over_limit"] is None


def test_only_open_idle_conversations_are_offered_for_archiving_biggest_first(
    tmp_path: Path, _isolated: Path
) -> None:
    folders, owners = three_worktrees(_isolated)
    fresh = make_worktree(_isolated, "fresh", 9000)
    owners[owner_key(fresh)] = [
        WorktreeOwner("s-fresh", "just now", False, folder=str(fresh), idle_days=1.0)
    ]
    big = make_worktree(_isolated, "big", 8000, age_days=30)
    owners[owner_key(big)] = [
        WorktreeOwner("s-big", "big idle one", False, folder=str(big), idle_days=30.0)
    ]

    report = measure(tmp_path / "home", owners=owners, idle_days=14)

    offered = [c["session_id"] for c in report["archive_candidates"]]
    assert offered == ["s-big", "s-open"], "archived and recently active ones are not offered"
    assert report["archive_candidates"][0]["bytes"] == 8000
    assert report["archive_idle_days"] == 14


def test_a_worktree_a_run_in_this_process_is_using_is_never_offered(
    tmp_path: Path, _isolated: Path
) -> None:
    from chimera.core import worktree

    folder = make_worktree(_isolated, "running", 100, age_days=30)
    worktree._live_here.add(folder.resolve())
    try:
        owners = {owner_key(folder): [WorktreeOwner("s", "t", False, folder=str(folder), idle_days=30.0)]}
        report = measure(tmp_path / "home", owners=owners)
    finally:
        worktree._live_here.discard(folder.resolve())

    assert report["archive_candidates"] == []


def test_a_worktree_in_another_projects_folder_is_listed_through_its_conversation(
    tmp_path: Path, _isolated: Path
) -> None:
    elsewhere = make_worktree(tmp_path / "other-project-wts", "far", 4321)
    owners = {
        owner_key(elsewhere): [WorktreeOwner("s", "far work", False, folder=str(elsewhere), idle_days=1.0)]
    }

    report = measure(tmp_path / "home", owners=owners)

    assert rows_by_name(report)["far"]["bytes"] == 4321


def test_the_projects_durable_folder_is_listed_when_the_project_is_known(
    tmp_path: Path, _isolated: Path
) -> None:
    """The measured defect: without the project the 89 conversation checkouts were not looked for."""
    repo = tmp_path / "work" / "repo"
    repo.mkdir(parents=True)
    durable = make_worktree(repo.parent / DURABLE_DIR / "repo", "mine", 777)

    assert durable not in find_worktree_dirs(), "the call the prune uses is unchanged"
    assert durable in find_worktree_dirs(repo)
    assert rows_by_name(measure(tmp_path / "home", repo))["mine"]["bytes"] == 777


def test_a_worktree_size_counted_by_one_look_is_kept_for_the_next(
    tmp_path: Path, _isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_worktree(_isolated, "a", 100)
    calls: list[Path] = []
    real = storage.tree_size

    def counted(path: Path, **kw: object) -> Size:
        calls.append(Path(path))
        return real(path, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(storage, "tree_size", counted)
    cache: dict[str, tuple[float, Size]] = {}

    measure(tmp_path / "home", size_cache=cache)
    measure(tmp_path / "home", size_cache=cache)

    assert len([c for c in calls if c.name == "chimera-wt-a"]) == 1


def test_the_two_new_settings_take_a_positive_number_and_fall_back_otherwise() -> None:
    assert Settings().worktree_limit_gb == 50.0 and Settings().worktree_idle_days == 14.0
    assert Settings(CHIMERA_WORKTREE_LIMIT_GB="80").worktree_limit_gb == 80.0  # type: ignore[call-arg]
    for bad in ("", "lots", "0", "-3"):
        assert Settings(CHIMERA_WORKTREE_LIMIT_GB=bad).worktree_limit_gb == 50.0  # type: ignore[call-arg]
        assert Settings(CHIMERA_WORKTREE_IDLE_DAYS=bad).worktree_idle_days == 14.0  # type: ignore[call-arg]


def test_the_offer_is_the_archive_route_and_no_function_that_removes_a_folder_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over HTTP: the conversation shows up as a candidate, archiving it is the list's own route, the
    report then says it is archived with a date, the folder is untouched, and neither
    `prune_worktree_dirs` nor `sweep_expired` nor `prune_worktrees` was called by any of it."""
    import chimera.core.worktree as worktree
    import chimera.core.worktree_expiry as expiry
    import chimera.core.worktree_prune as prune_module
    from tests.conversation_fakes import Agent, App

    removal_calls: list[str] = []
    for module, name in (
        (worktree, "prune_worktree_dirs"),
        (expiry, "sweep_expired"),
        (prune_module, "prune_worktrees"),
    ):
        monkeypatch.setattr(module, name, lambda *a, _n=name, **k: removal_calls.append(_n))

    Agent.hold.clear()
    Agent.held.clear()
    app = App(tmp_path, monkeypatch, Agent, CHIMERA_WORKTREE_IDLE_DAYS="1")
    app.folder = make_worktree(tmp_path / "wts", "conv", 123, age_days=10)
    sid = app.turn("hello")
    long_ago = time.time() - 10 * 86400
    os.utime(app.home / "code_sessions" / f"{sid}.json", (long_ago, long_ago))
    # No look at the conversation list here: ITS own background sweep (the thirty-day clean-up) is
    # the existing mechanism and is not what this test is about; the offer must not call it itself.

    before = app.client.get("/api/storage").json()
    assert [c["session_id"] for c in before["archive_candidates"]] == [sid]
    assert before["archive_candidates"][0]["idle_days"] >= 9
    row = next(w for w in before["worktrees"] if w["path"] == str(app.folder))
    assert row["retention"] == "conversation"

    assert app.client.post(f"/api/code/sessions/{sid}/archive").status_code == 200
    storage.forget_shared_reports()
    after = app.client.get("/api/storage").json()

    row = next(w for w in after["worktrees"] if w["path"] == str(app.folder))
    assert row["retention"] == "archived" and row["retention_due_at"] is not None
    assert after["archive_candidates"] == []
    assert app.folder.is_dir() and (app.folder / "file.bin").exists(), "archiving removed nothing"
    assert removal_calls == []
