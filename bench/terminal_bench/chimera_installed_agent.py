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

ROOT CAUSE of the earlier ``agent_timeout`` (confirmed by reading the harness, 2026-07-08): it is
**not** a tmux end-detection bug — ``perform_task`` below already bypasses the fragile tmux
completion signal by driving everything through the container's synchronous ``exec_run``. The
timeout is the harness's per-task ``max_agent_timeout_sec`` (each task's yaml), enforced by
``asyncio.wait_for`` around ``perform_task`` (``harness.py`` ``_run_agent_with_timeout``). When the
solve takes longer than that limit, TB records ``FailureMode.AGENT_TIMEOUT`` regardless of our own
``timeout 600``. Two honest ways forward:
  - **validation** (prove the grading path works end-to-end): pass ``--global-agent-timeout-sec <big>``
    to the ``tb run`` so the solve finishes and TB actually runs the task tests -> a real
    is_resolved verdict instead of agent_timeout.
  - **leaderboard-honest** (Phase 2): respect each task's ``max_agent_timeout_sec`` and report
    whatever the agent completes within it (a fast model or a lighter scaffold), OR document the
    ``--global-agent-timeout`` override transparently. Never silently inflate the budget.

VALIDATED (2026-07-08, Phase 0): oracle scored 100% on fix-git; the chimera agent graded on fix-git
with ``--global-agent-timeout-sec 1100`` -> is_resolved false, failure_mode "unset" (no agent_timeout).

PHASE 1 — config locked (2026-07-08). Two integration bugs found and fixed on real green passes:
  1. **Workdir**: the solve now runs ``cd /app`` first. The TB client container's working dir is /app
     and tests assert absolute paths under it (hello-world checks /app/hello.txt); exec_run defaulted
     to the image WORKDIR (/), so the agent wrote files where the grader never looked -> false. Fixed
     -> **hello-world is_resolved TRUE** (first real chimera pass).
  2. **Install portability**: task base images are heterogeneous (some ship pip, some a bare
     /usr/bin/python3 with no pip/ensurepip, some mark the env PEP-668 externally-managed, some lack
     curl AND wget). Install is now a bootstrap chain — network PyPI first (``chimera-agent`` is
     public; resolves the container's own ABI) via ``python3 -m pip`` with ``--break-system-packages``,
     bootstrapping pip through ensurepip / a urllib-fetched get-pip.py when absent, then the offline
     cp313 wheelhouse as the last resort. Fixed the ``agent_installation_failed`` on
     fix-permissions/csv-to-parquet -> **fix-permissions is_resolved TRUE**.
Config: model ``openrouter/deepseek/deepseek-chat-v3.1``, native per-task timeout (360s for 56/80),
``CHIMERA_SOLVE_TIMEOUT=300``, the lean scaffold flags. Timeout FITS natively — no override needed.
Real verdicts now flow (hello-world+fix-permissions PASS, fix-git+csv-to-parquet FAIL) — the honest
mix Phase 2 measures at scale. Next: Phase 2 (baseline vs chimera A/B, N≈30-50). See PLAN.md.
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

# Install chimera into the task container. Prefer a NETWORK install from PyPI (chimera-agent is
# public now) because it resolves the right wheels for THIS container's Python/ABI — the offline
# cp313 wheelhouse fails on task images with a different Python. Fall back to the wheelhouse when
# the container has no network (some tasks are offline).
_INSTALL = """#!/bin/bash
mkdir -p /chimera/wh
echo CHIMERA_INSTALL_START
PY=$(command -v python3 || command -v python)
L=/chimera/pip.log
BSP=--break-system-packages  # PEP 668: task images mark the system env externally-managed; safe in a throwaway container
# Ensure pip exists, then install. Task base images vary widely: some ship pip (hello-world), others
# a bare /usr/bin/python3 with neither pip nor ensurepip (fix-permissions/csv-to-parquet: Debian
# minimal — pip lives in the apt package python3-pip), and some lack curl AND wget entirely. Bootstrap
# chain: pip module -> ensurepip -> get-pip.py fetched via python's own urllib (no curl/wget needed) ->
# curl/wget as a last resort. `python3 -m pip` (module) not the `pip` wrapper (often absent from PATH).
if ! $PY -m pip --version >$L 2>&1; then
  $PY -m ensurepip --default-pip >>$L 2>&1 || \\
  { $PY -c "import urllib.request as u;u.urlretrieve('https://bootstrap.pypa.io/get-pip.py','/chimera/get-pip.py')" >>$L 2>&1 || \\
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /chimera/get-pip.py 2>>$L || \\
    wget -qO /chimera/get-pip.py https://bootstrap.pypa.io/get-pip.py 2>>$L; \\
    $PY /chimera/get-pip.py $BSP >>$L 2>&1; } || true
fi
if $PY -m pip install $BSP --quiet chimera-agent >>$L 2>&1; then
  echo CHIMERA_INSTALL_OK_NET
else
  echo "--- net install failed; falling back to offline wheelhouse ---" >>$L
  tar xf /installed-agent/wheelhouse.tar -C /chimera/wh 2>>$L \\
    && $PY -m pip install $BSP --no-index --find-links=/chimera/wh chimera-agent >>$L 2>&1 \\
    && echo CHIMERA_INSTALL_OK_WH || { echo CHIMERA_INSTALL_FAILED; exit 1; }
fi
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

            if logging_dir is not None:  # capture WHY install failed (no network + ABI-mismatched wheelhouse?)
                try:
                    _rc, out = container.exec_run(
                        ["bash", "-lc", "cat /chimera/pip.log 2>/dev/null | tail -c 4000; "
                         "echo; echo '---PY---'; python3 --version"],
                        environment=env,
                    )
                    ld = Path(str(logging_dir))
                    ld.mkdir(parents=True, exist_ok=True)
                    (ld / "chimera_install_fail.log").write_bytes(
                        out if isinstance(out, bytes) else str(out).encode("utf-8", "replace")
                    )
                except Exception:
                    pass
            return AgentResult(failure_mode=FailureMode.AGENT_INSTALLATION_FAILED)

        # stdin from /dev/null + output to a file so chimera never touches the terminal; a shell-level
        # `timeout` bounds it even though exec_run is already synchronous. CRITICAL: cd into /app first
        # — the Terminal-Bench client container's working directory is /app, and the task tests check
        # absolute paths under /app (e.g. hello-world asserts /app/hello.txt). exec_run defaults to the
        # image WORKDIR (often /), so without this the agent writes files where the grader never looks.
        solve = (
            f"cd /app 2>/dev/null; timeout {int(_SOLVE_TIMEOUT)} chimera solve {shlex.quote(instruction)} "
            f"--workspace . --model {self._model_name} {_FLAGS} < /dev/null > /tmp/csolve.log 2>&1"
        )
        container.exec_run(["bash", "-lc", solve], environment=env)

        # Copy the solve log + pip log out to the host run dir so we can diagnose *why* a task scored
        # 0 (the container is torn down after grading). Best-effort — never fail the run over logging.
        if logging_dir is not None:
            try:
                dump = "cat /tmp/csolve.log 2>/dev/null | tail -c 12000; " \
                    "echo; echo '---PIP---'; cat /chimera/pip.log 2>/dev/null | tail -c 3000"
                _rc, out = container.exec_run(["bash", "-lc", dump], environment=env)
                ld = Path(str(logging_dir))
                ld.mkdir(parents=True, exist_ok=True)
                data = out if isinstance(out, bytes) else str(out).encode("utf-8", "replace")
                (ld / "chimera_solve.log").write_bytes(data)
            except Exception:
                pass
        return AgentResult(total_input_tokens=0, total_output_tokens=0)

    def _run_agent_commands(self, instruction: str) -> list[TerminalCommand]:
        # Not used — perform_task drives chimera via container.exec_run (see above). Kept because the
        # AbstractInstalledAgent ABC requires it.
        return []
