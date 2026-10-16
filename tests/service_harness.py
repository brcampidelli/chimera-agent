"""Run the real ``chimera service`` in a throwaway home, and never leave a process behind.

Every test that starts a service does it through :class:`ServiceSandbox`: its own ``CHIMERA_HOME``,
``CHIMERA_SERVICE_DIR``, bridge folder and user home (so the owner's ``~/.chimera/.env`` and their
real record are never read), no inherited ``CHIMERA_*`` variable, no cron and no bots. ``close``
kills every pid the sandbox saw — the record's and every ``spawned_pid`` a ``start`` reported — so
a failing assertion cannot leave a server running on the machine.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from chimera.core.service_record import ServiceRecord, pid_alive, read_record

#: Generous: a cold interpreter importing the whole app on a loaded Windows runner.
START_TIMEOUT = 120.0


class ServiceSandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.service_dir = root / "svc"
        self.user = root / "user"
        for folder in (self.home, self.service_dir, self.user):
            folder.mkdir(parents=True, exist_ok=True)
        self.pids: set[int] = set()

    @property
    def record_path(self) -> Path:
        return self.service_dir / "service-dev.json"

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("CHIMERA_")}
        env.update(
            CHIMERA_HOME=str(self.home),
            CHIMERA_SERVICE_DIR=str(self.service_dir),
            CHIMERA_SERVICE_CHANNEL="dev",
            CHIMERA_BRIDGE_DIR=str(self.service_dir),
            CHIMERA_APP_CRON="0",
            CHIMERA_APP_MESSAGING="0",
            CHIMERA_MCP_AUTOLOAD="0",
            CHIMERA_MEMORY_BACKEND="json",
            USERPROFILE=str(self.user),
            HOME=str(self.user),
            PYTHONUTF8="1",
            PYTHONIOENCODING="utf-8",
        )
        return env

    def cli(self, *args: str, timeout: float = START_TIMEOUT + 30) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(
            [sys.executable, "-m", "chimera.cli.main", "service", *args],
            cwd=self.root, env=self.env(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        self._remember(done.stdout)
        return done

    def start(self) -> dict[str, Any]:
        done = self.cli("start", "--port", "0", "--json", "--timeout", str(START_TIMEOUT))
        assert done.returncode == 0, f"start failed:\n{done.stdout}\n{done.stderr}\n{self.log()}"
        return dict(json.loads(done.stdout.strip().splitlines()[-1]))

    def record(self) -> ServiceRecord | None:
        found = read_record(self.record_path)
        if found is not None:
            self.pids.add(found.pid)
        return found

    def log(self) -> str:
        with contextlib.suppress(OSError):
            return (self.home / "logs" / "service.log").read_text(encoding="utf-8", errors="replace")
        return ""

    def _remember(self, stdout: str) -> None:
        for line in stdout.splitlines():
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                data = json.loads(line)
                for key in ("pid", "spawned_pid"):
                    if isinstance(data.get(key), int):
                        self.pids.add(data[key])

    def close(self) -> None:
        self.record()
        for pid in self.pids:
            kill(pid)


def kill(pid: int) -> None:
    if not pid_alive(pid) or pid == os.getpid():
        return
    with contextlib.suppress(Exception):
        import psutil

        psutil.Process(pid).kill()
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, 9)


def wait_dead(pid: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    return not pid_alive(pid)
