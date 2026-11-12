"""An archived conversation's worktree goes thirty days after the later of archiving and last opening.

`chimera/core/worktree_expiry.py`. The clock, the candidate rules and the verdict bookkeeping are
checked on their own; the removal itself runs against real git worktrees made by `GitWorktree.create`,
so the refusals are the owner's cleanup's: a dirty tree and an unpushed commit keep the folder, a
clean pushed one goes, and the switch off or an unarchive before the time keeps everything.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from chimera.core import worktree_expiry as ex
from chimera.core.code_session_marks import CodeSessionMarks, ConversationMark
from chimera.core.worktree import GitWorktree

DAY = 86400.0
NOW = 1_000_000_000.0


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout.strip()


@pytest.fixture(autouse=True)
def _identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }.items():
        monkeypatch.setenv(name, value)


# -- the clock --------------------------------------------------------------------------------


def test_due_is_thirty_days_after_archiving_when_never_opened() -> None:
    mark = ConversationMark(archived_at=NOW)
    assert ex.removal_due_at(mark) == NOW + 30 * DAY


def test_due_runs_from_the_later_opening() -> None:
    mark = ConversationMark(archived_at=NOW, opened_at=NOW + 10 * DAY)
    assert ex.removal_due_at(mark) == NOW + 40 * DAY


def test_an_opening_before_archiving_does_not_shorten_the_clock() -> None:
    mark = ConversationMark(archived_at=NOW, opened_at=NOW - 20 * DAY)
    assert ex.removal_due_at(mark) == NOW + 30 * DAY


def test_no_clock_when_not_archived_or_already_removed() -> None:
    assert ex.removal_due_at(ConversationMark()) is None
    assert ex.removal_due_at(ConversationMark(archived_at=NOW, worktree_removed_at=NOW)) is None


def test_unarchiving_before_the_time_keeps_the_worktree() -> None:
    mark = ConversationMark(archived_at=None, unarchived_at=NOW)
    assert ex.removal_due_at(mark) is None
    assert ex.has_expiry_work({"s": mark}, now=NOW + 365 * DAY) is False


def test_the_view_is_shown_only_for_a_conversation_worktree_and_when_enabled() -> None:
    mark = ConversationMark(archived_at=NOW)
    folder = "/x/.chimera-worktrees/chimera-wt-feat-1234"
    assert ex.removal_view(mark, workspace=folder, enabled=True) == {
        "due_at": NOW + 30 * DAY, "kept": "",
    }
    assert ex.removal_view(mark, workspace=folder, enabled=False) is None
    assert ex.removal_view(mark, workspace="/x/project", enabled=True) is None
    assert ex.removal_view(ConversationMark(), workspace=folder, enabled=True) is None


def test_the_view_shows_what_kept_it() -> None:
    mark = ConversationMark(archived_at=NOW, worktree_kept="dirty")
    view = ex.removal_view(mark, workspace="/x/chimera-wt-a", enabled=True)
    assert view is not None and view["kept"] == "dirty"


# -- candidates and throttling ----------------------------------------------------------------


def test_candidates_are_due_ones_only_and_a_kept_one_waits_a_day() -> None:
    snapshot = {
        "due": ConversationMark(archived_at=NOW - 31 * DAY),
        "early": ConversationMark(archived_at=NOW - 5 * DAY),
        "kept-today": ConversationMark(
            archived_at=NOW - 31 * DAY, worktree_kept="dirty", worktree_checked_at=NOW - 3600
        ),
        "kept-old": ConversationMark(
            archived_at=NOW - 31 * DAY, worktree_kept="dirty", worktree_checked_at=NOW - 2 * DAY
        ),
    }
    folders = {sid: Path(f"/w/chimera-wt-{sid}") for sid in snapshot}

    found = ex.candidates(snapshot, folder_of=folders.get, now=NOW)

    assert [sid for sid, _ in found] == ["due", "kept-old"]


def test_a_folder_not_named_like_a_conversation_worktree_is_never_a_candidate() -> None:
    snapshot = {"s": ConversationMark(archived_at=NOW - 60 * DAY)}
    assert ex.candidates(snapshot, folder_of=lambda _s: Path("/p/project"), now=NOW) == []


# -- the sweep, with the removal stubbed ------------------------------------------------------


class FakePrune:
    def __init__(self, kept: str = "", removed: bool = True) -> None:
        self.kept = kept
        self.removed = removed
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, folder: Path, paths: list[str], **kwargs: Any) -> list[Any]:
        self.calls.append((folder, tuple(paths), kwargs["dry_run"]))
        from chimera.core.worktree_prune import PruneResult

        return [PruneResult(path=str(folder), kept=self.kept, removed=self.removed)]


def _marks(tmp_path: Path) -> CodeSessionMarks:
    return CodeSessionMarks(tmp_path / "marks.json")


def test_a_removed_worktree_is_recorded_and_never_asked_again(tmp_path: Path) -> None:
    folder = tmp_path / "chimera-wt-a"
    folder.mkdir()
    marks = _marks(tmp_path)
    marks.archive("a", at=NOW - 40 * DAY)
    prune = FakePrune()

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=lambda _s: folder,
        held=lambda _s: False, now=NOW, prune=prune,
    )

    assert removed == ["a"]
    assert prune.calls == [(folder, (str(folder),), False)]  # the real removal, not a dry run
    assert marks.get("a").worktree_removed_at == NOW
    assert ex.removal_due_at(marks.get("a")) is None


def test_a_refused_worktree_is_kept_and_the_refusal_is_written(tmp_path: Path) -> None:
    folder = tmp_path / "chimera-wt-b"
    folder.mkdir()
    marks = _marks(tmp_path)
    marks.archive("b", at=NOW - 40 * DAY)

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=lambda _s: folder,
        held=lambda _s: False, now=NOW, prune=FakePrune(kept="dirty", removed=False),
    )

    assert removed == []
    assert folder.is_dir()
    mark = marks.get("b")
    assert mark.worktree_kept == "dirty"
    assert mark.worktree_checked_at == NOW
    assert mark.worktree_removed_at is None


def test_a_held_conversation_is_left_alone_and_nothing_is_written(tmp_path: Path) -> None:
    folder = tmp_path / "chimera-wt-c"
    folder.mkdir()
    marks = _marks(tmp_path)
    marks.archive("c", at=NOW - 40 * DAY)
    prune = FakePrune()

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=lambda _s: folder,
        held=lambda _s: True, now=NOW, prune=prune,
    )

    assert removed == [] and prune.calls == []
    assert marks.get("c").worktree_checked_at is None


def test_a_folder_already_gone_is_closed_out_without_a_removal(tmp_path: Path) -> None:
    marks = _marks(tmp_path)
    marks.archive("d", at=NOW - 40 * DAY)
    prune = FakePrune()

    ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=lambda _s: tmp_path / "chimera-wt-gone",
        held=lambda _s: False, now=NOW, prune=prune,
    )

    assert prune.calls == []
    assert marks.get("d").worktree_removed_at == NOW


# -- the marks it writes ----------------------------------------------------------------------


def test_opened_writes_at_most_once_an_hour(tmp_path: Path) -> None:
    marks = _marks(tmp_path)
    marks.archive("e", at=NOW)
    marks.opened("e", at=NOW + 10)
    marks.opened("e", at=NOW + 600)  # within the hour: no write
    assert marks.get("e").opened_at == NOW + 10
    marks.opened("e", at=NOW + 3600 + 10)
    assert marks.get("e").opened_at == NOW + 3610


# -- the real removal against git -------------------------------------------------------------


def _repo_with_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    main = tmp_path / "main"
    main.mkdir()
    _git(tmp_path, "init", "-b", "main", str(main))
    (main / "a.txt").write_text("a\n", encoding="utf-8")
    _git(main, "add", "a.txt")
    _git(main, "commit", "-m", "first")
    _git(main, "remote", "add", "origin", str(origin))
    _git(main, "push", "-u", "origin", "main")
    return main


def _conversation_worktree(main: Path, tmp_path: Path, name: str) -> Path:
    made = GitWorktree.create(
        main, branch=f"feat/{name}", durable=True, label=name,
        app_registry=tmp_path / "registry.json", start_point="HEAD",
    )
    return made.path


def test_a_clean_pushed_worktree_goes_and_a_dirty_one_stays(tmp_path: Path) -> None:
    main = _repo_with_origin(tmp_path)
    clean = _conversation_worktree(main, tmp_path, "clean")
    dirty = _conversation_worktree(main, tmp_path, "dirty")
    (dirty / "scratch.txt").write_text("unsaved\n", encoding="utf-8")
    marks = _marks(tmp_path)
    marks.archive("clean", at=NOW - 40 * DAY)
    marks.archive("dirty", at=NOW - 40 * DAY)
    folders = {"clean": clean, "dirty": dirty}

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=folders.get,
        held=lambda _s: False, now=NOW,
    )

    assert removed == ["clean"]
    assert not clean.exists()
    assert dirty.is_dir() and (dirty / "scratch.txt").exists()
    assert marks.get("dirty").worktree_kept == "dirty"


def test_an_unpushed_commit_keeps_its_worktree(tmp_path: Path) -> None:
    main = _repo_with_origin(tmp_path)
    wt = _conversation_worktree(main, tmp_path, "unpushed")
    (wt / "work.txt").write_text("work\n", encoding="utf-8")
    _git(wt, "add", "work.txt")
    _git(wt, "commit", "-m", "work")
    marks = _marks(tmp_path)
    marks.archive("u", at=NOW - 40 * DAY)

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=lambda _s: wt,
        held=lambda _s: False, now=NOW,
    )

    assert removed == []
    assert wt.is_dir()
    # A branch nobody pushed has no upstream: git-side refusal `no_upstream` (kept, never forced).
    assert marks.get("u").worktree_kept == "no_upstream"


# -- the first sweep does not collect what was archived before the feature --------------------


def test_the_clock_starts_no_earlier_than_the_activation() -> None:
    mark = ConversationMark(archived_at=NOW - 100 * DAY)
    assert ex.removal_due_at(mark) == NOW - 70 * DAY  # without a floor: long overdue
    assert ex.removal_due_at(mark, activated_at=NOW) == NOW + 30 * DAY
    view = ex.removal_view(mark, workspace="/x/chimera-wt-a", enabled=True, activated_at=NOW)
    assert view == {"due_at": NOW + 30 * DAY, "kept": ""}


def test_a_conversation_archived_long_ago_is_not_a_candidate_on_the_first_sweep() -> None:
    snapshot = {"old": ConversationMark(archived_at=NOW - 100 * DAY)}
    folders = {"old": Path("/w/chimera-wt-old")}
    assert ex.candidates(snapshot, folder_of=folders.get, now=NOW, activated_at=NOW) == []
    assert ex.has_expiry_work(snapshot, now=NOW, activated_at=NOW) is False
    assert ex.candidates(snapshot, folder_of=folders.get, now=NOW + 31 * DAY, activated_at=NOW) != []


def test_the_activation_is_written_once_and_never_moves(tmp_path: Path) -> None:
    clock_now = [NOW]
    path = tmp_path / "since.json"
    first = ex.ActivationClock(path, clock=lambda: clock_now[0])
    assert first.value(enabled=False) is None  # switch off: no floor, nothing to write
    assert first.value(enabled=True) == NOW and first.dirty
    first.persist()
    assert not first.dirty

    clock_now[0] = NOW + 90 * DAY
    again = ex.ActivationClock(path, clock=lambda: clock_now[0])
    again.load()
    assert again.value(enabled=True) == NOW and not again.dirty


def test_has_expiry_work_waits_a_day_after_a_look() -> None:
    kept = ConversationMark(archived_at=NOW - 40 * DAY, worktree_kept="dirty", worktree_checked_at=NOW - 3600)
    assert ex.has_expiry_work({"k": kept}, now=NOW) is False
    kept.worktree_checked_at = NOW - 2 * DAY
    assert ex.has_expiry_work({"k": kept}, now=NOW) is True


# -- the race: the record is read again, under its lock, right before the removal -------------


def test_a_conversation_brought_back_after_the_snapshot_is_not_removed(tmp_path: Path) -> None:
    folder = tmp_path / "chimera-wt-r"
    folder.mkdir()
    marks = _marks(tmp_path)
    marks.archive("r", at=NOW - 40 * DAY)
    stale = marks.all()
    marks.unarchive("r", at=NOW - 1)  # a turn reopened it after the list took its snapshot
    prune = FakePrune()

    removed = ex.sweep_expired(
        marks, snapshot=stale, folder_of=lambda _s: folder, held=lambda _s: False, now=NOW, prune=prune,
    )

    assert removed == [] and prune.calls == []


def test_a_reopening_during_the_removal_cancels_it(tmp_path: Path) -> None:
    folder = tmp_path / "chimera-wt-q"
    folder.mkdir()
    marks = _marks(tmp_path)
    marks.archive("q", at=NOW - 40 * DAY)

    def prune(_folder: Path, _paths: list[str], **kwargs: Any) -> list[Any]:
        from chimera.core.worktree_prune import PruneResult

        marks.unarchive("q", at=NOW)  # the turn arrives while the refusals are being read
        busy = kwargs["busy"](folder)
        return [PruneResult(path=str(folder), kept="in_use" if busy else "", removed=not busy)]

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=lambda _s: folder, held=lambda _s: False, now=NOW, prune=prune,
    )

    assert removed == [] and folder.is_dir()
    assert marks.get("q").worktree_removed_at is None


# -- a folder that is not there ---------------------------------------------------------------


def test_a_missing_folder_on_a_missing_drive_is_not_closed_out_as_removed(tmp_path: Path) -> None:
    marks = _marks(tmp_path)
    marks.archive("m", at=NOW - 40 * DAY)
    off = tmp_path / "no-drive" / "worktrees" / "chimera-wt-m"  # neither it nor its parents exist

    ex.sweep_expired(marks, snapshot=marks.all(), folder_of=lambda _s: off, held=lambda _s: False, now=NOW)

    mark = marks.get("m")
    assert mark.worktree_removed_at is None and mark.worktree_kept == ex.UNAVAILABLE


# -- the unattended removal keeps an ignored file ---------------------------------------------


def _repo_ignoring_local(tmp_path: Path) -> Path:
    main = _repo_with_origin(tmp_path)
    (main / ".gitignore").write_text("*.local\n*.sqlite\n.env*\n", encoding="utf-8")
    _git(main, "add", ".gitignore")
    _git(main, "commit", "-m", "ignore")
    _git(main, "push", "origin", "main")
    return main


def test_an_ignored_file_made_in_the_conversation_keeps_the_worktree(tmp_path: Path) -> None:
    main = _repo_ignoring_local(tmp_path)
    wt = _conversation_worktree(main, tmp_path, "ign")
    (wt / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
    assert _git(wt, "status", "--porcelain") == ""  # git calls it clean
    marks = _marks(tmp_path)
    marks.archive("ign", at=NOW - 40 * DAY)

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=lambda _s: wt, held=lambda _s: False, now=NOW,
    )

    assert removed == [] and (wt / ".env.local").read_text(encoding="utf-8") == "SECRET=1\n"
    assert marks.get("ign").worktree_kept == "ignored_files"


def test_a_file_worktreeinclude_copied_does_not_keep_it_until_it_is_edited(tmp_path: Path) -> None:
    from chimera.core import worktree_include as wi

    main = _repo_ignoring_local(tmp_path)
    (main / ".env").write_text("A=1\n", encoding="utf-8")
    (main / ".worktreeinclude").write_text(".env\n", encoding="utf-8")
    kept = _conversation_worktree(main, tmp_path, "edited")
    gone = _conversation_worktree(main, tmp_path, "copied")
    for wt in (kept, gone):
        assert wi.copy_included(main, wt, consent=lambda _d, _p: True).copied == [".env"]
    (kept / ".env").write_text("A=2\n", encoding="utf-8")  # the conversation changed it
    marks = _marks(tmp_path)
    marks.archive("edited", at=NOW - 40 * DAY)
    marks.archive("copied", at=NOW - 40 * DAY)
    folders = {"edited": kept, "copied": gone}

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=folders.get, held=lambda _s: False, now=NOW,
    )

    assert removed == ["copied"] and not gone.exists()
    assert kept.is_dir() and marks.get("edited").worktree_kept == "ignored_files"


def test_the_owners_dialog_still_removes_a_worktree_with_an_ignored_file(tmp_path: Path) -> None:
    """The manual cleanup is unchanged: the owner sees the list and picks. Only the unattended one
    asks for `protect_ignored`."""
    from chimera.core.worktree_prune import prune_worktrees

    main = _repo_ignoring_local(tmp_path)
    wt = _conversation_worktree(main, tmp_path, "manual")
    (wt / "cache.sqlite").write_text("x\n", encoding="utf-8")

    manual = prune_worktrees(main, [str(wt)], dry_run=True)
    guarded = prune_worktrees(main, [str(wt)], dry_run=True, protect_ignored=True)

    assert manual is not None and manual[0].would_remove is True
    assert guarded is not None and guarded[0].kept == "ignored_files"


# -- the folder is protected, not only the conversation ---------------------------------------


def test_a_folder_another_live_conversation_uses_is_kept(tmp_path: Path) -> None:
    main = _repo_with_origin(tmp_path)
    wt = _conversation_worktree(main, tmp_path, "shared")
    marks = _marks(tmp_path)
    marks.archive("old", at=NOW - 40 * DAY)  # archived and due
    # "live" uses the same folder and was never archived
    users = {"old": wt, "live": wt}

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=lambda sid: users[sid], held=lambda _s: False, now=NOW,
        folder_users=lambda _folder: ["old", "live"],
    )

    assert removed == [] and wt.is_dir()


def test_a_folder_whose_other_conversation_has_a_turn_is_kept_and_a_due_pair_goes(tmp_path: Path) -> None:
    main = _repo_with_origin(tmp_path)
    busy_wt = _conversation_worktree(main, tmp_path, "busy")
    pair_wt = _conversation_worktree(main, tmp_path, "pair")
    marks = _marks(tmp_path)
    for sid in ("a", "b", "c", "d"):
        marks.archive(sid, at=NOW - 40 * DAY)
    folders = {"a": busy_wt, "b": busy_wt, "c": pair_wt, "d": pair_wt}

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=folders.get, held=lambda sid: sid == "b", now=NOW,
        folder_users=lambda folder: [s for s, f in folders.items() if f == folder],
    )

    assert busy_wt.is_dir()  # "b" has a turn: the folder "a" shares stays
    assert not pair_wt.exists() and set(removed) >= {"c"}


# -- never forced, never a branch that was not pushed -----------------------------------------


def test_the_sweep_never_forces_and_never_deletes_an_unpushed_branch(tmp_path: Path) -> None:
    from chimera.core.pull_request import run_quiet

    main = _repo_with_origin(tmp_path)
    pushed = _conversation_worktree(main, tmp_path, "pushed")
    marks = _marks(tmp_path)
    marks.archive("p", at=NOW - 40 * DAY)
    seen: list[list[str]] = []

    def runner(args: Any, cwd: Path) -> Any:
        seen.append([str(a) for a in args])
        return run_quiet(args, cwd)

    from chimera.core.worktree_prune import prune_worktrees

    def prune(folder: Path, paths: list[str], **kwargs: Any) -> Any:
        return prune_worktrees(folder, paths, runner=runner, **kwargs)

    removed = ex.sweep_expired(
        marks, snapshot=marks.all(), folder_of=lambda _s: pushed, held=lambda _s: False, now=NOW, prune=prune,
    )

    assert removed == ["p"]
    flat = [a for call in seen for a in call]
    assert "--force" not in flat and "-f" not in flat and "-D" not in flat and "push" not in flat
    # The branch was never pushed (no upstream): the folder went, the branch stays.
    assert _git(main, "branch", "--list", "feat/pushed") != ""
