"""Persistence for scheduled jobs (a small JSON-file store)."""

from __future__ import annotations

import json
from pathlib import Path

from chimera.scheduler.models import CronJob
from chimera.telemetry import get_logger

_log = get_logger("scheduler.store")


class CronStore:
    """A JSON-file-backed collection of :class:`CronJob` objects."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._jobs: dict[str, CronJob] = {}
        self._mtime: float | None = None
        self.load()

    def load(self) -> None:
        self._jobs = {}
        if not self.path.exists():
            self._mtime = None
            return
        self._mtime = self.path.stat().st_mtime
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        for item in raw:
            # Skip a single malformed entry (hand-edit, version skew, partial write) instead of
            # letting it abort the whole load — one bad line must not silently drop every other cron.
            try:
                job = CronJob.model_validate(item)
            except ValueError as exc:
                _log.warning("skipping malformed cron entry: %s", exc)
                continue
            self._jobs[job.id] = job

    def reload_if_changed(self) -> bool:
        """Reload from disk if the file changed since the last load or save.

        Lets a long-lived daemon pick up jobs that another process (e.g. ``chimera cron
        add`` in a separate shell/container) wrote to the same file, without a restart.
        Returns ``True`` when a reload happened. Our own :meth:`save` refreshes the tracked
        mtime, so the daemon never reloads on account of its own writes.
        """
        try:
            mtime = self.path.stat().st_mtime if self.path.exists() else None
        except OSError:
            return False
        if mtime != self._mtime:
            self.load()
            return True
        return False

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [job.model_dump() for job in self._jobs.values()]
        # Atomic write (temp + replace): the crontab is safety-critical — a plain write_text truncates
        # the file first, so a crash mid-write or a concurrent reader would see 0 bytes and drop every
        # cron. os.replace is atomic on the same filesystem. (Matches providers/cache.py's convention.)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._mtime = self.path.stat().st_mtime

    def add(self, job: CronJob) -> None:
        self._jobs[job.id] = job
        self.save()

    def get(self, job_id: str) -> CronJob:
        return self._jobs[job_id]

    def list(self) -> list[CronJob]:
        return list(self._jobs.values())

    def remove(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
        self.save()

    def __contains__(self, job_id: object) -> bool:
        return job_id in self._jobs

    def __len__(self) -> int:
        return len(self._jobs)
