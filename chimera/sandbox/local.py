"""Local sandbox — runs commands directly on the host (timeout + working dir only).

The default backend and the fallback when Docker is unavailable. It is *not* isolated;
the governance kernel gates what reaches it, and DockerSandbox provides real isolation.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import suppress
from pathlib import Path

from chimera.proc.stdio import kill_tree
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
    @staticmethod
    def is_isolated() -> bool:
        """The local sandbox runs on the host — no isolation from it."""
        return False

    def _command_argv(self, command: str, cwd: Path | None) -> tuple[list[str] | str, bool]:
        """What to hand ``Popen``, and whether it needs a shell. Runs the command as typed.

        The seam :class:`~chimera.sandbox.os_sandbox.OsSandbox` overrides to wrap the same command
        in the platform's kernel sandbox. It exists so that everything below — the timeout that
        kills the whole process tree, the secret-scrubbed environment, the non-interactive
        overrides — is written once. Each of those is a correctness fix with its own history, and a
        sandbox backend that copied them would be a second place for that history to rot.
        """
        return (command, True)

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        posix = os.name == "posix"
        target, use_shell = self._command_argv(command, cwd)
        # start_new_session puts the command in its own process GROUP so a timeout can kill the whole
        # tree (killpg), not just the shell — otherwise a forked grandchild survives the timeout and
        # can even hold the stdout pipe open, hanging the reap past the deadline.
        proc = subprocess.Popen(
            target,
            shell=use_shell,
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
            # `kill_tree` rather than a second copy of the same logic. This branch reimplemented the
            # POSIX half and stopped there: on Windows it was a bare `proc.kill()`, which kills the
            # shell and leaves whatever the shell started — `npm test` dies, the node workers holding
            # the workspace do not. A timeout that orphans the processes it was meant to stop is the
            # failure it exists to prevent, and it only ever happened on Windows, which is where this
            # project is developed.
            kill_tree(proc)
            with suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=5)
            return SandboxResult(
                exit_code=124, stderr=f"command timed out after {timeout}s", timed_out=True
            )
        return SandboxResult(exit_code=proc.returncode, stdout=out or "", stderr=err or "")
