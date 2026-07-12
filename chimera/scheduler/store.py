"""Persistence for scheduled jobs (a small JSON-file store)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

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

    def _read_disk(self) -> dict[str, CronJob] | None:
        """Parse the on-disk jobs, or ``None`` if the file is absent, unreadable, or not a valid JSON
        list — so the caller can keep whatever it already holds rather than wipe every cron on a single
        stray character. A single malformed *entry* is skipped (the rest of a valid list still loads).
        """
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        except (OSError, ValueError) as exc:
            # A truncated/typo'd/half-written file must NOT abort the load and drop every cron.
            _log.warning("cron store unreadable or not valid JSON, keeping current jobs: %s", exc)
            return None
        if not isinstance(raw, list):
            _log.warning("cron store root is not a JSON list, keeping current jobs")
            return None
        jobs: dict[str, CronJob] = {}
        for item in raw:
            try:
                job = CronJob.model_validate(item)
            except ValueError as exc:
                _log.warning("skipping malformed cron entry: %s", exc)
                continue
            jobs[job.id] = job
        return jobs

    def load(self) -> None:
        disk = self._read_disk()
        if disk is None:
            if not self.path.exists():
                self._jobs = {}
                self._mtime = None
            # else: present but unreadable/invalid — keep current in-memory jobs AND leave _mtime
            # unchanged, so a later fix to the file (mtime bump) triggers a fresh reload attempt
            # rather than being masked by an already-advanced mtime.
            return
        self._jobs = disk
        self._mtime = self.path.stat().st_mtime

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

    def _fold_in_external(self) -> None:
        """Before a write, pull in any jobs another process added since our last load, so dumping our
        in-memory snapshot doesn't silently drop a concurrent ``cron add`` (the daemon may hold a
        minutes-old snapshot while dispatching a long job). Best-effort: it closes the common
        data-loss window but is not a substitute for an OS lock — a write racing an external *remove*
        can still briefly resurrect that job until the next reload. Full multi-writer safety would need
        file locking, which this deliberately-small store leaves out.
        """
        disk = self._read_disk()
        if disk is None:
            return
        for jid, job in disk.items():
            self._jobs.setdefault(jid, job)  # only ADD unknown jobs; never clobber our own updates

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [job.model_dump() for job in self._jobs.values()]
        # Atomic write (unique temp + replace): the crontab is safety-critical — a plain write_text
        # truncates first, so a crash mid-write or a concurrent reader would see 0 bytes and drop every
        # cron. A UNIQUE per-write temp name (pid+uuid) stops two concurrent writers from clobbering a
        # shared "jobs.json.tmp" mid-write; os.replace is atomic on the same filesystem.
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)  # no-op after a successful replace; cleans up on any failure
        self._mtime = self.path.stat().st_mtime

    def add(self, job: CronJob) -> None:
        self._fold_in_external()
        self._jobs[job.id] = job
        self.save()

    def get(self, job_id: str) -> CronJob:
        return self._jobs[job_id]

    def list(self) -> list[CronJob]:
        return list(self._jobs.values())

    def remove(self, job_id: str) -> None:
        self._fold_in_external()
        self._jobs.pop(job_id, None)
        self.save()

    def __contains__(self, job_id: object) -> bool:
        return job_id in self._jobs

    def __len__(self) -> int:
        return len(self._jobs)
