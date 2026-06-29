"""Persistence for scheduled jobs (a small JSON-file store)."""

from __future__ import annotations

import json
from pathlib import Path

from chimera.scheduler.models import CronJob


class CronStore:
    """A JSON-file-backed collection of :class:`CronJob` objects."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._jobs: dict[str, CronJob] = {}
        self.load()

    def load(self) -> None:
        self._jobs = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        for item in raw:
            job = CronJob.model_validate(item)
            self._jobs[job.id] = job

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [job.model_dump() for job in self._jobs.values()]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

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
