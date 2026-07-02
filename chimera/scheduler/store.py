"""Persistence for scheduled jobs (a small JSON-file store)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from chimera.scheduler.models import CronJob


class CronStore:
    """A JSON-file-backed collection of :class:`CronJob` objects.

    The file is the source of truth. A long-running daemon calls
    :meth:`reload_if_changed` each tick so out-of-band edits (``chimera cron
    add/remove/enable/disable``) take effect without a restart, instead of the
    daemon firing a stale in-memory snapshot. Writes are atomic (temp + rename)
    so a concurrent reader never sees a half-written file.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._jobs: dict[str, CronJob] = {}
        self._sig: tuple[float, int] | None = None
        self.load()

    def load(self) -> None:
        self._jobs = {}
        if not self.path.exists():
            self._sig = None
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        for item in raw:
            job = CronJob.model_validate(item)
            self._jobs[job.id] = job
        self._sig = self._stat_sig()

    def reload_if_changed(self) -> bool:
        """Reload from disk when the file changed since the last read.

        Returns ``True`` if a reload happened. This is what makes a running
        daemon honour CLI edits (the rollback primitive): disabling a job on
        disk stops the daemon firing it on the next tick. Change is detected by
        an ``(mtime, size)`` signature — size flips on enable/disable/add/remove,
        which catches edits that land within one mtime resolution tick.
        """
        if self._stat_sig() != self._sig:
            self.load()
            return True
        return False

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [job.model_dump() for job in self._jobs.values()]
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic; readers never see a partial file
        self._sig = self._stat_sig()

    def _stat_sig(self) -> tuple[float, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def add(self, job: CronJob) -> None:
        self._jobs[job.id] = job
        self.save()

    def get(self, job_id: str) -> CronJob:
        return self._jobs[job_id]

    def get_or_none(self, job_id: str) -> CronJob | None:
        return self._jobs.get(job_id)

    def list(self) -> list[CronJob]:
        return list(self._jobs.values())

    def remove(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
        self.save()

    def __contains__(self, job_id: object) -> bool:
        return job_id in self._jobs

    def __len__(self) -> int:
        return len(self._jobs)
