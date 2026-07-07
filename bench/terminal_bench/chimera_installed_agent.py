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
        # Deliver the wheelhouse tarball before the base copies + runs the install script.
        session.copy_to_container(  # type: ignore[attr-defined]
            Path(_TAR), container_dir="/installed-agent", container_filename="wheelhouse.tar"
        )
        return super().perform_task(instruction, session, logging_dir)  # type: ignore[arg-type]

    def _run_agent_commands(self, instruction: str) -> list[TerminalCommand]:
        # Make completion detectable by TB's tmux pane-watcher:
        #   TERM=dumb NO_COLOR=1   -> rich/textual emit no cursor/ANSI control that scrambles the pane
        #   > /tmp/csolve.log 2>&1 -> chimera's output goes to a file, leaving the pane clean
        #   timeout N              -> a hard shell-level cap even if TB's own detection lags
        #   stty sane; echo DONE   -> recover the terminal and print a clean line so the prompt returns
        timeout_s = int(_SOLVE_TIMEOUT)
        cmd = (
            f"TERM=dumb NO_COLOR=1 timeout {timeout_s} "
            f"chimera solve {shlex.quote(instruction)} "
            f"--workspace . --model {self._model_name} {_FLAGS} > /tmp/csolve.log 2>&1; "
            f"stty sane 2>/dev/null; echo CHIMERA_SOLVE_DONE"
        )
        return [
            TerminalCommand(
                command=cmd, block=True, max_timeout_sec=_SOLVE_TIMEOUT + 30, append_enter=True
            )
        ]
