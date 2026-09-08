"""Chimera as a LoopsBench agent — a third-party ruler for our own loop.

LoopsBench (arXiv 2608.00267, ``microsoft/Loopsbench``, MIT) is the first ruler we run that we did
not write. That is the point: every suite this project authored for the loop landed at a ceiling
(``bench/learning_lift``: 84-92% across three attempts to build a 40-60% band) or a floor
(``bench/terminal_bench``: 7.5% / 2.5%, 37 of 40 failing both arms). A bench with no discriminating
middle cannot answer whether the loop helps, in either direction.

**Plugged in, not forked.** ``AgentFactory.get_agent_class`` takes an ``import_path`` of the form
``module:ClassName``, so this file is ours, lives in our repo, and the upstream harness is used
unmodified. Point ``--agent-import-path`` here with this directory on ``PYTHONPATH``.

Everything below except the container handle is ported from
``bench/terminal_bench/chimera_installed_agent.py``, which solved the same problem — get Chimera into
a heterogeneous task container and run it without corrupting the harness's terminal. Its lessons are
kept rather than re-derived:

1. **Drive through ``exec_run``, not the tmux pane.** ``chimera solve`` renders a rich/textual UI;
   writing that into the pane the harness reads is how a run becomes unparseable. Output goes to a
   file and the pane never sees it. (LoopsBench hands us a ``TmuxSession``; we take its container.)
2. **Network install first, ABI-correct by construction.** Task base images differ in Python
   version, so a prebuilt wheelhouse is the *fallback*, never the default — a cp313 wheelhouse fails
   on a cp312 image. The bootstrap chain exists because task images ship, variously, no pip, no
   ensurepip, and neither curl nor wget.
3. **``cd`` to the workspace the grader reads.** On Terminal-Bench this cost a false negative: the
   agent wrote correct files where the tests never looked. LoopsBench task instructions say
   ``Your working directory is /workspace``, so that is where the solve runs.
4. **Copy the logs out before the container dies.** A task that scores 0 with no log is a number
   with no diagnosis, and the container is torn down after grading.

Config by env: ``OPENROUTER_API_KEY`` (required), ``CHIMERA_LB_MODEL``, ``CHIMERA_LB_FLAGS``,
``CHIMERA_LB_SOLVE_TIMEOUT``.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from loopsbench.agents.base_agent import AgentResult, BaseAgent
from loopsbench.terminal.tmux_session import TmuxSession

_MODEL = os.environ.get("CHIMERA_LB_MODEL", "openrouter/deepseek/deepseek-chat-v3.1")
#: ``--max-steps 60``, not the shipped default of 8. A LoopsBench task is a multi-module
#: implementation behind a dependency DAG with a two-hour budget; eight tool calls cannot finish one
#: under any loop, so leaving the default would have measured the step limit and reported it as the
#: loop. See Amendment 1 in PREREGISTRATION.md — registered before spending, not after seeing a zero.
_FLAGS = os.environ.get(
    "CHIMERA_LB_FLAGS",
    "--repo-map --progress-ledger --checklist --max-attempts 1 --max-steps 60 "
    "--no-remember --no-collect --no-evolve-skills --no-manager --keep-workspace",
)
#: ``--keep-workspace`` is not a tweak — it is the flag Chimera has for exactly this situation, and
#: its own help says so: *"On failure, leave the last attempt's edits on disk for an external grader
#: (don't revert)."* LoopsBench IS the external grader. Without it, verify-or-revert rolls the tree
#: back before the task's tests run: measured on run p4, **20 of 23 rounds** logged
#: ``trabalho revertido`` and the harness then graded a tree the agent had been made to undo.
#: `bench/terminal_bench` does not pass it either, so this omission is older than this run.
#: Ceiling on one solve. Set high on purpose so the **task's own** ``max_agent_timeout_sec`` is what
#: binds: a 1500 s cap on a 7200 s task truncates the arm at 21% of its allowance and the miss reads
#: as incapability. The mirror of the Terminal-Bench rule "never silently inflate the budget".
_SOLVE_TIMEOUT = float(os.environ.get("CHIMERA_LB_SOLVE_TIMEOUT", "7200"))

_WORKDIR = "/workspace"

_INSTALL = r"""#!/bin/bash
mkdir -p /chimera
echo CHIMERA_INSTALL_START

# 0. Idempotent, because LoopsBench's outer loop re-invokes the agent in the SAME container: the
#    harness runs round-01, round-02, round-03 against one workspace, and this script runs each
#    time. Measured on round-02 of the compiler task: `uv venv` refuses an existing environment
#    ("A virtual environment already exists"), the install returned non-zero, and the round was
#    recorded as agent_installation_failed — so a task got ONE round of agent work instead of three
#    and the shortfall was invisible in the pass rate. Re-downloading a 32 MB interpreter every
#    round would also be waste. If the launcher already works, there is nothing to install.
if [ -x /chimera/run ] && /chimera/run --help >/dev/null 2>&1; then
  echo CHIMERA_INSTALL_ALREADY_PRESENT
  echo CHIMERA_LAUNCHER_OK
  exit 0
fi

PY=$(command -v python3 || command -v python)
L=/chimera/pip.log
echo "python: $($PY -VV 2>&1)" >>$L

# 1. Make sure pip exists at all. Task images ship, variously, no pip, no ensurepip, and neither
#    curl nor wget — hence the chain rather than one command.
if ! $PY -m pip --version >>$L 2>&1; then
  $PY -m ensurepip --default-pip >>$L 2>&1 || \
  { $PY -c "import urllib.request as u;u.urlretrieve('https://bootstrap.pypa.io/get-pip.py','/chimera/get-pip.py')" >>$L 2>&1 || \
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /chimera/get-pip.py 2>>$L || \
    wget -qO /chimera/get-pip.py https://bootstrap.pypa.io/get-pip.py 2>>$L; \
    $PY /chimera/get-pip.py >>$L 2>&1 || \
    $PY /chimera/get-pip.py --break-system-packages >>$L 2>&1; } || true
fi
$PY -m pip --version >>$L 2>&1

# 2. Pass --break-system-packages ONLY where pip understands it. pip < 23 does not have the option
#    and treats it as a hard error, not a warning — so an image with an older pip fails every
#    install with "no such option" and the harness records agent_installation_failed. Measured on
#    task_compiler_fdmj_llvm: Python 3.10, pip 22.0.2. The flag exists for PEP-668 images, and every
#    one of those is newer than pip 23 anyway, so asking pip what it supports costs nothing.
BSP=""
if $PY -m pip install --help 2>&1 | grep -q -- "--break-system-packages"; then
  BSP="--break-system-packages"
fi
echo "BSP='$BSP'" >>$L

# 3. chimera-agent requires Python >=3.11 and task images do not all ship one: measured, the
#    compiler task's image is Python 3.10.12, where PyPI correctly reports "no matching
#    distribution". Dropping such tasks would select the subset toward containers that happen to
#    carry a new interpreter — a selection nobody registered, on a bench whose whole value is that
#    we did not choose its tasks. So bring our own interpreter instead.
#
#    uv comes from PyPI, not from astral.sh: the install script there answers urllib with HTTP 403
#    and these images have no curl, while pip reaches PyPI fine — measured, both ways, in the image
#    that failed.
#    ALWAYS a venv, never the container's system Python. Three different images broke three
#    different ways when installing into the system interpreter, and each was found only by running
#    it: pip 22 rejecting the flag, Python 3.10 having no compatible wheel, and — the third —
#    `Cannot uninstall Pygments 2.17.2, RECORD file not found. Hint: The package was installed by
#    debian`, where pip cannot replace a distro-packaged dependency. A private environment has none
#    of these failure modes and does not depend on what a task image happens to ship, so it replaces
#    the branch that tried to guess which images were safe.
$PY -m pip install $BSP --quiet uv >>$L 2>&1 || { echo CHIMERA_UV_INSTALL_FAILED; exit 1; }
UV=$($PY -c 'import uv;print(uv.find_uv_bin())' 2>>$L) || { echo CHIMERA_UV_BIN_MISSING; exit 1; }
"$UV" venv --python 3.12 /chimera/venv >>$L 2>&1 || { echo CHIMERA_UV_VENV_FAILED; exit 1; }
"$UV" pip install --python /chimera/venv/bin/python --quiet chimera-agent >>$L 2>&1 \
  || { echo CHIMERA_UV_PIP_FAILED; exit 1; }
echo "exec /chimera/venv/bin/chimera \"\$@\"" > /chimera/run
echo CHIMERA_INSTALL_OK_UV

# 4. A launcher rather than a bare `chimera`, because `pip install` puts the console script in
#    /usr/local/bin on some images and ~/.local/bin on others, and the second is not always on PATH
#    for a non-login shell. A run that installs correctly and then cannot find its own binary is the
#    same zero as one that never installed, told apart by nothing.
chmod +x /chimera/run
/chimera/run --help >/dev/null 2>>$L && echo CHIMERA_LAUNCHER_OK || { echo CHIMERA_LAUNCHER_FAILED; exit 1; }
"""


class ChimeraAgent(BaseAgent):
    """Install ``chimera-agent`` into the task container, then run one ``chimera solve``."""

    def __init__(self, model_name: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model_name = model_name or _MODEL
        self._flags = str(kwargs.get("flags", _FLAGS))
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise ValueError(
                "OPENROUTER_API_KEY is not set. Refusing to start: every task would install "
                "chimera, fail to reach a provider, and score 0 — a floor that looks like a "
                "result. Set the key, or run --agent oracle to exercise the harness for free."
            )

    @staticmethod
    def name() -> str:
        return "chimera"

    def get_trajectory_paths(self) -> list[str]:
        return []

    def _env(self) -> dict[str, str]:
        return {
            "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
            "CHIMERA_DEFAULT_MODEL": str(self._model_name),
            # No memory, no skill evolution, no telemetry home shared across tasks: each task is an
            # independent trial and anything carried between them would make the 8 rows dependent.
            "CHIMERA_HOME": "/chimera/home",
            # THE ONE THAT DECIDED A WHOLE RUN. `host_exec` defaults to "ask"; task images have no
            # bubblewrap, so there is no OS sandbox, and there is no TTY to answer the question —
            # so Chimera correctly refused to run a single command. Measured on run p3: **10 of 21
            # rounds** carried "refusing to run the agent's commands", and two tasks carried it in
            # all three. On a benchmark whose tasks are "build with `make all` and pass the tests",
            # the agent could edit files and execute nothing, and the resulting 0/7 was a
            # measurement of that, not of the loop.
            #
            # `allow` is right here and nowhere else: the container IS the sandbox, it is destroyed
            # after grading, and "the host" from Chimera's point of view is that throwaway container.
            "CHIMERA_HOST_EXEC": "allow",
        }

    def perform_task(
        self,
        instruction: str,
        session: TmuxSession,
        logging_dir: Path | None = None,
        timeout_sec: float | None = None,
    ) -> AgentResult:
        container = session._container  # the harness's own handle; the oracle agent uses it too
        env = self._env()

        install_rc, _out = container.exec_run(["bash", "-lc", _INSTALL], environment=env)
        if install_rc != 0:
            self._dump_logs(container, env, logging_dir, tag="install-failed")
            # Named, not swallowed: an install failure and a genuine 0 are different facts, and a
            # run that cannot tell them apart reports infrastructure as incapability.
            return AgentResult(failure_mode="agent_installation_failed")

        budget = _SOLVE_TIMEOUT
        if timeout_sec is not None and timeout_sec > 0:
            # Stay inside the harness's own budget with room to write the log out.
            budget = max(60.0, min(_SOLVE_TIMEOUT, float(timeout_sec) - 60.0))

        solve = (
            f"cd {_WORKDIR} 2>/dev/null; "
            f"timeout {int(budget)} /chimera/run solve {shlex.quote(instruction)} "
            f"--workspace . --model {self._model_name} {self._flags} "
            f"< /dev/null > /chimera/solve.log 2>&1"
        )
        container.exec_run(["bash", "-lc", solve], environment=env)
        self._dump_logs(container, env, logging_dir, tag="solve")

        # Token counts are left at 0 deliberately: `chimera solve` reports its spend to its own
        # receipt inside the container, and copying a number we have not verified crosses the
        # boundary would put an unchecked figure into the harness's cost column. The receipt is in
        # the dumped log; read it there rather than trusting a field nobody validated.
        return AgentResult()

    @staticmethod
    def _dump_logs(
        container: Any, env: dict[str, str], logging_dir: Path | None, *, tag: str
    ) -> None:
        """Copy the solve and pip logs out. Best effort — never fail a paid run over logging."""
        if logging_dir is None:
            return
        try:
            dump = (
                "echo '--- SOLVE ---'; tail -c 20000 /chimera/solve.log 2>/dev/null; "
                "echo; echo '--- PIP ---'; tail -c 4000 /chimera/pip.log 2>/dev/null; "
                "echo; echo '--- RECEIPT ---'; "
                "tail -c 4000 /chimera/home/runs.jsonl 2>/dev/null"
            )
            _rc, out = container.exec_run(["bash", "-lc", dump], environment=env)
            ld = Path(str(logging_dir))
            ld.mkdir(parents=True, exist_ok=True)
            data = out if isinstance(out, bytes) else str(out).encode("utf-8", "replace")
            (ld / f"chimera_{tag}.log").write_bytes(data)
        except Exception:
            pass
