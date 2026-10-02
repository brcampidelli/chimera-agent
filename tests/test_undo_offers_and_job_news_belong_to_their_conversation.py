"""An undo offer and a finished job's news each belong to the conversation (or project) they came from.

Found reading the code on 2026-09-30 (R7 and R8 of the review of several conversations at once).

- Undo offers lived in one module-level dict capped at 8 for the whole app. After eight editing turns
  anywhere, an older conversation's Undo button, and a finished background work's undo, answered
  "nothing to undo". Each offer also held a copy of the whole folder in memory.
- "A background job finished" was handed to the next turn of ANY project, which marked it reported;
  that turn could not read the job's output (the job tools are fenced to the turn's folder), and the
  project that started the job never heard.

Now offers are kept per conversation (20 each, 200 in all, oldest first out) and hold only the files the
turn changed; and a turn is told only about jobs that ran inside its own folder.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.undo_offers import UndoOffers
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.jobs import Job, JobRegistry, jobs_for
from chimera.interface import ChatSession


def test_eight_edits_elsewhere_do_not_take_away_another_conversations_undo() -> None:
    offers = UndoOffers(per_session=20, total=200)
    mine = offers.offer("conversation-a", "a's undo")
    for n in range(9):
        offers.offer("conversation-b", f"b's undo {n}")

    assert offers.take(mine) == "a's undo"
    assert offers.take(mine) is None, "an offer is taken once"


def test_a_conversation_keeps_its_most_recent_offers_and_the_app_keeps_a_total() -> None:
    offers = UndoOffers(per_session=2, total=3)
    first = offers.offer("a", 1)
    second = offers.offer("a", 2)
    third = offers.offer("a", 3)
    assert offers.take(first) is None, "the conversation's oldest offer goes first"
    assert offers.take(second) == 2 and offers.take(third) == 3

    kept = [offers.offer(f"s{n}", n) for n in range(4)]
    assert offers.take(kept[0]) is None, "past the total, the app's oldest offer goes"
    assert [offers.take(t) for t in kept[1:]] == [1, 2, 3]


def test_an_undo_offer_holds_only_the_files_its_turn_changed(tmp_path: Path) -> None:
    for n in range(50):
        (tmp_path / f"untouched_{n}.txt").write_text("x" * 100, encoding="utf-8")
    (tmp_path / "a.txt").write_text("before", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    (tmp_path / "a.txt").write_text("after", encoding="utf-8")

    change = guard.diff_since(before)

    assert change.before.present == {"a.txt"}, "the offer kept a copy of the whole folder"
    assert guard.restore_change(change).restored == 1
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "before"


def _finished(registry: JobRegistry, job_id: str, cwd: Path) -> None:
    job = Job(id=job_id, command="make", cwd=str(cwd), pid=0, started_at=time.time(), log="",
              state="finished", exit_code=0, finished_at=time.time())
    registry._write(job)


def test_a_turn_hears_only_about_jobs_that_ran_in_its_own_folder(tmp_path: Path) -> None:
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    (project_a / "sub").mkdir(parents=True)
    project_b.mkdir()
    registry = JobRegistry(tmp_path / "home")
    _finished(registry, "job-a", project_a / "sub")
    _finished(registry, "job-b", project_b)

    heard_in_b = registry.finished_unreported(within=project_b)

    assert [j.id for j in heard_in_b] == ["job-b"]
    assert [j.id for j in registry.finished_unreported(within=project_a)] == ["job-a"], (
        "a turn in another project must not mark project A's job as reported"
    )
    assert registry.finished_unreported(within=project_a) == [], "each job is reported once"


class _Recorder:
    tasks: list[str] = []

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, **_kw: Any) -> AgentResult:
        # The finished-jobs note travels in the turn's notes (AgentConfig.turn_notes), not the task.
        type(self).tasks.append(task + " | " + str(getattr(self.config, "turn_notes", "") or ""))
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


def test_the_route_tells_a_turn_only_about_its_own_projects_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Recorder, raising=True)
    _Recorder.tasks = []
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    client = TestClient(build_api_app(lambda: ChatSession(_Recorder()), workspace=project_a, settings=settings))
    _finished(jobs_for(home), "job-in-a", project_a)

    client.post("/api/code/turn", json={"message": "in b", "workspace": str(project_b), "stream": False})
    client.post("/api/code/turn", json={"message": "in a", "workspace": str(project_a), "stream": False})

    in_b, in_a = _Recorder.tasks
    assert "job-in-a" not in in_b, "project B's turn was told about project A's job"
    assert "job-in-a" in in_a, "project A's turn never heard about its own job"


class _Writer:
    """Writes a new line into edits.txt on every turn and reports it, like an editing turn."""

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, on_edit: Any = None, **_kw: Any) -> AgentResult:
        folder = Path(task.split(" in ", 1)[1])
        target = folder / "edits.txt"
        previous = target.read_text(encoding="utf-8") if target.exists() else ""
        target.write_text(previous + task + "\n", encoding="utf-8")
        if on_edit is not None:
            on_edit("edits.txt", "@@")
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


def test_a_conversations_undo_survives_many_editing_turns_in_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import re

    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Writer, raising=True)
    folder_a, folder_b = tmp_path / "a", tmp_path / "b"
    folder_a.mkdir()
    folder_b.mkdir()
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    client = TestClient(build_api_app(lambda: ChatSession(_Writer()), workspace=folder_a, settings=settings))

    def turn(folder: Path, session: str | None) -> tuple[str, str]:
        body: dict[str, Any] = {"message": f"edit in {folder}", "workspace": str(folder), "stream": False}
        if session:
            body["session_id"] = session
        text = client.post("/api/code/turn", json=body).text
        token = re.search(r'"revert_token": ?"([0-9a-f]+)"', text).group(1)  # type: ignore[union-attr]
        sid = re.search(r'"session_id": ?"([^"]+)"', text).group(1)  # type: ignore[union-attr]
        return token, sid

    token_a, _ = turn(folder_a, None)
    _, session_b = turn(folder_b, None)
    for _ in range(21):
        turn(folder_b, session_b)

    result = client.post(f"/api/code/revert/{token_a}").json()

    assert result["ok"] is True, "another conversation's editing turns took this conversation's undo away"
    assert not (folder_a / "edits.txt").exists()
