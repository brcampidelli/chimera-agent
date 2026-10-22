"""A running service keeps its record true, and steps down when the record says someone else (S1).

Every :data:`INTERVAL` seconds the service reads its own discovery file:

* gone (a cleanup, a user deleting ``~/.chimera``) — it writes itself back, so a client started a
  minute later still finds it;
* naming another id whose process is alive — another service took this channel, so this one shuts
  down instead of answering clients the file no longer sends to it;
* naming another id whose process is dead — litter; it writes itself back.

On the way out it deletes the record only if the record is still its own. A server that removed
the file unconditionally would erase a successor that had already published.

This is the second line, not the first: two services on one data folder are already impossible
(``chimera/core/instance.py`` claims the folder before anything is built). The watch covers what the
claim cannot see — one channel's record shared by two data folders.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from chimera.core.service_record import (
    ServiceRecord,
    load_or_create_token,
    new_record,
    pid_alive,
    read_record,
    record_path,
    remove_record_if_ours,
    save_token,
    write_record,
)
from chimera.telemetry import get_logger

_log = get_logger("core.service_watch")

INTERVAL = 5.0

Outcome = Literal["ok", "rewrote", "stepped_down"]


class RecordWatch:
    """Re-reads the record every ``interval`` seconds; calls ``on_step_down`` once if displaced."""

    def __init__(
        self,
        record: ServiceRecord,
        on_step_down: Callable[[], None],
        *,
        path: Path | None = None,
        interval: float = INTERVAL,
        alive: Callable[[int], bool] = pid_alive,
    ) -> None:
        self.record = record
        self.path = path or record_path()
        self._on_step_down = on_step_down
        self._interval = interval
        self._alive = alive
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stepped_down = False
        self._lock = threading.Lock()

    def publish(self) -> None:
        write_record(self.record, self.path)

    def check_once(self) -> Outcome:
        with self._lock:
            if self.stepped_down:
                return "stepped_down"
            current = read_record(self.path)
            if current is not None and current.id == self.record.id:
                if current.stopping and not self.record.stopping:
                    self.record = replace(self.record, stopping=True)
                return "ok"
            if current is not None and current.pid != os.getpid() and self._alive(current.pid):
                self.stepped_down = True
                _log.warning(
                    "service record now names %s (pid %s); stepping down", current.id, current.pid
                )
            elif current is None or current.id != self.record.id:
                write_record(self.record, self.path)
                return "rewrote"
        self._on_step_down()
        return "stepped_down"

    def mark_stopping(self) -> None:
        """Keep discovery visible while clients wait for this process to exit."""
        with self._lock:
            current = read_record(self.path)
            if current is None or current.id != self.record.id:
                return
            self.record = replace(self.record, stopping=True)
            write_record(self.record, self.path)

    def replace_token(self, token: str) -> None:
        with self._lock:
            self.record = replace(self.record.with_token(token), stopping=self.record.stopping)
            if not self.stepped_down:
                write_record(self.record, self.path)

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self._interval):
                try:
                    if self.check_once() == "stepped_down":
                        return
                except OSError as exc:  # a full disk must not kill the service; next tick retries
                    _log.warning("service record check failed: %s", exc)

        self._thread = threading.Thread(target=loop, name="service-record-watch", daemon=True)
        self._thread.start()

    def close(self, *, remove: bool = True) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
        if remove:
            with contextlib.suppress(OSError):
                remove_record_if_ours(self.record.id, self.path)


def service_token(server_token: str | None, home: Path) -> str:
    """The token this service requires and publishes, set where the API's guard reads it.

    An owner-chosen ``CHIMERA_SERVER_TOKEN`` (environment or ``.env``) wins. Otherwise the folder's
    persistent ``service.token``, put into the environment before the app is served: the guard reads
    ``get_settings().server_token`` on every request. Children never inherit it — the shell and the
    code tools strip every variable whose name says TOKEN (``chimera/core/redact.py``).
    """
    if server_token:
        return server_token
    token = load_or_create_token(home)
    os.environ["CHIMERA_SERVER_TOKEN"] = token
    from chimera.config import get_settings

    get_settings.cache_clear()
    return token


@dataclass
class ServicePublication:
    """The record and its watch, for one serving process. ``close`` first thing in ``finally``."""

    home: Path
    watch: RecordWatch
    #: What else ends with the publication, first: the attached windows and their screen lease.
    closers: list[Callable[[], None]] = field(default_factory=list)

    @classmethod
    def start(
        cls, *, url: str, token: str, home: Path, on_step_down: Callable[[], None]
    ) -> ServicePublication:
        watch = RecordWatch(new_record(url=url, token=token, home=home), on_step_down)
        watch.publish()
        watch.start()
        return cls(home=home, watch=watch)

    @property
    def record(self) -> ServiceRecord:
        return self.watch.record

    def token_changed(self, value: str) -> None:
        """``PATCH /api/config`` saved a new ``CHIMERA_SERVER_TOKEN``: environment, file, record.

        The environment because it outranks ``.env``: the value this process put there at boot
        would otherwise keep winning over the one the owner just saved. An empty value falls back
        to the folder's own token — a service is never served without one.
        """
        token = value.strip() or load_or_create_token(self.home)
        os.environ["CHIMERA_SERVER_TOKEN"] = token
        from chimera.config import get_settings

        get_settings.cache_clear()
        save_token(self.home, token)
        self.watch.replace_token(token)

    def mark_stopping(self) -> None:
        self.watch.mark_stopping()

    def close(self, *, remove: bool = True) -> None:
        if not remove:
            self.watch._stop.set()
            if self.watch._thread is not None:
                self.watch._thread.join(timeout=self.watch._interval + 1)
            import atexit

            atexit.register(self.finish_exit)
        for close in self.closers:
            with contextlib.suppress(Exception):
                close()
        self.watch.close(remove=remove)

    def finish_exit(self) -> None:
        """Remove the discovery record only at the final boundary of process shutdown."""
        self.watch.close(remove=True)

    def begin_shutdown(self) -> None:
        """Stop publication before the other service resources are torn down."""
        self.mark_stopping()
        self.close(remove=False)

    def finish_shutdown(self, home: Path) -> None:
        """Stop scheduled jobs and remove the service record at the final shutdown boundary."""
        from chimera.core.jobs import stop_jobs_for

        stop_jobs_for(home)
        self.finish_exit()
