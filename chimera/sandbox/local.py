"""Local sandbox — runs commands directly on the host (timeout + working dir only).

The default backend and the fallback when Docker is unavailable. It is *not* isolated;
the governance kernel gates what reaches it, and DockerSandbox provides real isolation.
"""

from __future__ import annotations

import os
import signal
import subprocess
from contextlib import suppress
from pathlib import Path

from chimera.sandbox.base import SandboxResult

# Env-var name fragments that mark a secret — scrubbed from the child env so an injected/rogue command
# can't `echo $OPENROUTER_API_KEY` and exfiltrate provider keys (the gateway exports them to os.environ).
_SECRET_MARKERS = ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "PRIVATE_KEY")


def _child_env() -> dict[str, str]:
    """os.environ minus anything that looks like a secret, plus the non-interactive overrides."""
    env = {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SECRET_MARKERS)}
    env.update(_NONINTERACTIVE_ENV)
    return env

# Non-interactive execution env — the single biggest cause of an autonomous agent "hanging" is a
# command that blocks on input it will never get: git opening an editor/pager or asking for
# credentials, apt/read waiting on stdin, an accidental REPL. Combined with stdin=DEVNULL (so a read
# gets EOF instead of blocking), these turn every such stall into an instant, bounded result instead
# of burning the whole per-command timeout. A correctness fix, not just speed — it's how a single
# hard task could eat a 600s budget one 60s-timeout at a time.
_NONINTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",  # git never prompts for credentials
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_EDITOR": "true",  # `git commit`/`rebase` never opens $EDITOR and blocks
    "EDITOR": "true",
    "DEBIAN_FRONTEND": "noninteractive",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PYTHONUNBUFFERED": "1",
    "CI": "1",
}


class LocalSandbox:
    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        posix = os.name == "posix"
        # start_new_session puts the command in its own process GROUP so a timeout can kill the whole
        # tree (killpg), not just the shell — otherwise a forked grandchild survives the timeout and
        # can even hold the stdout pipe open, hanging the reap past the deadline.
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
            env=_child_env(),
            start_new_session=posix,
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if posix:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # type: ignore[attr-defined]
                except (ProcessLookupError, PermissionError):
                    proc.kill()
            else:
                proc.kill()
            with suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=5)
            return SandboxResult(
                exit_code=124, stderr=f"command timed out after {timeout}s", timed_out=True
            )
        return SandboxResult(exit_code=proc.returncode, stdout=out or "", stderr=err or "")
