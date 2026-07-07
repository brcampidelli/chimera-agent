"""Terminal-Bench installed-agent for the Chimera treatment arm — the working integration.

This is the real fix for the "chimera does not exist in Harbor's minimal task container" gap, proven
end-to-end on the official Terminal-Bench harness (WSL Ubuntu + Docker Desktop, dataset
terminal-bench-core==0.1.1). A ``terminal_bench.AbstractInstalledAgent`` that:

1. copies a self-built **wheelhouse** (chimera + all deps, built for the container's Python/arch,
   binary-only so no compiler is needed) into the task container as a single tarball, then
2. installs chimera **offline** from it (``pip install --no-index --find-links``) — no network, no
   PyPI, no private-repo auth in the container, then
3. runs ``chimera solve`` with its output redirected to a file (so its rich/textual UI never
   corrupts the tmux pane TB uses to detect command completion), under a finite timeout.

Harbor grades with the task's own tests afterwards — the verdict is never self-reported.

Build the wheelhouse to match the container base (terminal-bench-core is mostly the python-3-13
image, so cp313 linux). See README.md. Config via env:
  OPENROUTER_API_KEY        provider key (exported inside the container for chimera)
  CHIMERA_TB_MODEL          model slug (default: a cheap model)
  CHIMERA_WHEELHOUSE_TAR    host path to the wheelhouse tarball copied into the container
  CHIMERA_TB_FLAGS          solve flags (default: the M14 scaffolding at max-attempts 1)
  CHIMERA_SOLVE_TIMEOUT     per-solve command timeout in seconds (default 600)

HONEST FINDING (N=1, fix-git, deepseek-chat-v3.1): the integration produces a real graded result,
but chimera's thorough multi-step loop on a cheap model exceeded the task timeout -> is_resolved
false, failure_mode agent_timeout. The scaffolding's cost dominates on a time-bounded benchmark with
a weak model — the same lesson the local proxy + VPS runs surfaced. A non-timeout number needs a
larger per-task timeout (and hours of wall-clock + API for a full subset), or a faster model.
"""
from __future__ import annotations

import os
import shlex
import tempfile
from pathlib import Path

from terminal_bench.agents.installed_agents.abstract_installed_agent import (
    AbstractInstalledAgent,
)
from terminal_bench.terminal.models import TerminalCommand

_TAR = os.environ.get("CHIMERA_WHEELHOUSE_TAR", "/root/tbench/wheelhouse.tar")
_MODEL = os.environ.get("CHIMERA_TB_MODEL", "openrouter/deepseek/deepseek-chat-v3.1")
_FLAGS = os.environ.get(
    "CHIMERA_TB_FLAGS",
    "--repo-map --progress-ledger --checklist --max-attempts 1 "
    "--no-remember --no-collect --no-evolve-skills",
)
_SOLVE_TIMEOUT = float(os.environ.get("CHIMERA_SOLVE_TIMEOUT", "600"))

_INSTALL = """#!/bin/bash
set -e
echo CHIMERA_INSTALL_START
mkdir -p /chimera/wh
tar xf /installed-agent/wheelhouse.tar -C /chimera/wh
pip install --no-index --find-links=/chimera/wh chimera-agent >/chimera/pip.log 2>&1
echo CHIMERA_INSTALL_OK
"""


class ChimeraInstalledAgent(AbstractInstalledAgent):
    """Installs a chimera wheelhouse into each task container, then runs `chimera solve`."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._model_name = kwargs.get("model_name") or _MODEL

    @staticmethod
    def name() -> str:
        return "chimera"

    @property
    def _env(self) -> dict[str, str]:
        return {
            "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
            "CHIMERA_DEFAULT_MODEL": str(self._model_name),
        }

    @property
    def _install_agent_script_path(self) -> Path:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write(_INSTALL)
            name = f.name
        os.chmod(name, 0o755)
        return Path(name)

    def perform_task(self, instruction: str, session: object, logging_dir: object = None) -> object:
        # Run everything through the container's blocking `exec_run` (a direct docker exec), NOT the
        # tmux session. TB's tmux path signals completion by appending `; tmux wait -S done` and
        # watching the pane — fragile for a long, output-heavy command like `chimera solve` (the
        # pane state confuses the signal). `exec_run` returns exactly when the command finishes, with
        # its exit code, so there is no completion-detection to get wrong. Grading is unaffected: the
        # tests run against the same container's filesystem afterwards.
        from terminal_bench.agents.base_agent import AgentResult

        container = session.container  # type: ignore[attr-defined]
        env = self._env

        session.copy_to_container(  # type: ignore[attr-defined]
            Path(_TAR), container_dir="/installed-agent", container_filename="wheelhouse.tar"
        )
        install_rc, _ = container.exec_run(["bash", "-lc", _INSTALL], environment=env)
        if install_rc != 0:
            from terminal_bench.agents.failure_mode import FailureMode

            return AgentResult(failure_mode=FailureMode.AGENT_INSTALLATION_FAILED)

        # stdin from /dev/null + output to a file so chimera never touches the terminal; a shell-level
        # `timeout` bounds it even though exec_run is already synchronous.
        solve = (
            f"timeout {int(_SOLVE_TIMEOUT)} chimera solve {shlex.quote(instruction)} "
            f"--workspace . --model {self._model_name} {_FLAGS} < /dev/null > /tmp/csolve.log 2>&1"
        )
        container.exec_run(["bash", "-lc", solve], environment=env)
        return AgentResult(total_input_tokens=0, total_output_tokens=0)

    def _run_agent_commands(self, instruction: str) -> list[TerminalCommand]:
        # Not used — perform_task drives chimera via container.exec_run (see above). Kept because the
        # AbstractInstalledAgent ABC requires it.
        return []
