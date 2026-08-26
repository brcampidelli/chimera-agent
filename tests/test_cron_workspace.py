"""Which folder a scheduled job works in.

A schedule is written once and fires for months, so the root cannot be "wherever the app happened to
be pointing when it went off". Nothing recorded one, so every job ran at the process root — and on a
packaged desktop build that is the install directory.

Measured on a real install before this: *"list the project's files and say what changed today"*, at
07:00 daily, walked 4757 files of the app's own installation, was abandoned at the 1800s deadline
five nights running, and delivered nothing. The user's project was three folders away and the job
had no way to know it existed.
"""

from __future__ import annotations

from pathlib import Path

from chimera.scheduler import CronStore, Scheduler

NOW = 1_787_000_000.0


def _sched(tmp_path: Path) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "jobs.json"))


def test_a_job_remembers_the_folder_it_was_written_for(tmp_path: Path) -> None:
    job = _sched(tmp_path).schedule_cron(
        "resumo", "0 7 * * *", "liste os arquivos", now=NOW, workspace="/projects/cafe-aurora"
    )
    assert job.workspace == "/projects/cafe-aurora"


def test_it_survives_the_round_trip_to_disk(tmp_path: Path) -> None:
    """The field only matters at 7am tomorrow, which is a different process from the one that set it.

    A model field that is not persisted looks identical in every unit test and is empty in every
    real dispatch.
    """
    sched = _sched(tmp_path)
    job = sched.schedule_cron(
        "resumo", "0 7 * * *", "liste os arquivos", now=NOW, workspace="/projects/cafe-aurora"
    )
    de_novo = Scheduler(CronStore(tmp_path / "jobs.json")).store.get(job.id)
    assert de_novo.workspace == "/projects/cafe-aurora"


def test_no_folder_is_still_allowed_and_reads_as_absent(tmp_path: Path) -> None:
    """The control, and the compatibility promise.

    Every job written before this has no workspace, and the CLI creates jobs without one. Those keep
    the previous behaviour — the process root — so the field must round-trip as `None` rather than
    as an empty string that later reads as a chosen root of "".
    """
    job = _sched(tmp_path).schedule_cron("x", "0 7 * * *", "y", now=NOW)
    assert job.workspace is None
    assert Scheduler(CronStore(tmp_path / "jobs.json")).store.get(job.id).workspace is None


def test_a_job_saved_before_this_field_existed_still_loads(tmp_path: Path) -> None:
    """The store is a JSON file that outlives the schema. An older file must not fail to parse."""
    antigo = tmp_path / "jobs.json"
    antigo.write_text(
        '[{"id": "abc123", "name": "resumo", "trigger": "cron", "schedule": "0 7 * * *",'
        ' "action": "liste os arquivos", "created_by": "human", "enabled": true}]',
        encoding="utf-8",
    )
    job = Scheduler(CronStore(antigo)).store.get("abc123")
    assert job.workspace is None
    assert job.action == "liste os arquivos"
