"""Terminal-Bench 2.0 adapter — plug Chimera into the standard agent scoreboard.

Terminal-Bench is CLI/terminal-native (Docker task + instruction + verification tests, graded
pass/fail by those tests) and agent-agnostic via the Harbor harness. That makes it the honest
place to prove the thesis: run a FREE model alone (Harbor's neutral scaffold) vs the SAME model
driven by Chimera on the SAME task IDs, and report the delta (see :mod:`chimera.eval.bench_ab`).

This module is the treatment arm. :func:`build_solve_command` — the exact ``chimera solve``
invocation run inside a task container — is pure and unit-tested. :class:`ChimeraTBAgent` is the
thin ``terminal_bench.BaseAgent`` subclass Harbor imports; ``terminal_bench`` is imported lazily
(opt-in ``[bench]`` extra) so importing this module never requires it. The pass/fail verdict is
Harbor's — from the task's own tests — never something we self-report.
"""

from __future__ import annotations

import shlex
from typing import Any

# The scaffolding that IS Chimera's contribution: everything the thesis says lifts a weak model.
# Baseline (Arm A) is the raw model in Harbor's neutral scaffold and does NOT use this.
_DEFAULT_FLAGS: tuple[str, ...] = ("--repo-map", "--progress-ledger", "--replan")


def build_solve_command(
    instruction: str,
    *,
    model: str,
    workspace: str = ".",
    flags: tuple[str, ...] = _DEFAULT_FLAGS,
    max_attempts: int = 3,
) -> list[str]:
    """The argv for the ``chimera solve`` run that drives one benchmark task.

    Deterministic and side-effect-free so the treatment arm is reproducible and testable. The
    instruction is passed as a single argument (never shell-interpolated); ``flags`` are the
    scaffolding under test.
    """
    argv = ["chimera", "solve", instruction, "--model", model, "--workspace", workspace,
            "--max-attempts", str(max_attempts)]
    argv.extend(flags)
    return argv


def command_string(argv: list[str]) -> str:
    """Render an argv as a copy-pasteable shell string (for logs/transcripts)."""
    return " ".join(shlex.quote(a) for a in argv)


def container_bootstrap(wheel: str, *, workspace: str = "/app") -> str:
    """The prelude that makes `chimera` runnable INSIDE a Terminal-Bench task container.

    Harbor task containers are minimal — they have no `chimera`. So the treatment agent, before it
    can `chimera solve`, installs a self-built wheel that the run harness has placed in the container
    at ``wheel`` (a container-side path). Tries pip, then `python3 -m pip` as a fallback. The install
    is per-container and vanishes with it — nothing leaks into the host.
    """
    w = shlex.quote(wheel)
    ws = shlex.quote(workspace)
    return f"mkdir -p {ws} && (pip install --quiet --no-input {w} || python3 -m pip install --quiet --no-input {w})"


def build_container_command(
    instruction: str,
    *,
    model: str,
    wheel: str,
    workspace: str = "/app",
    flags: tuple[str, ...] = _DEFAULT_FLAGS,
    max_attempts: int = 3,
) -> str:
    """The single shell command the treatment agent runs in the task container: install + solve.

    No ``--verify`` is added — a Terminal-Bench agent never sees the task's tests; Harbor grades
    afterwards with them. So `chimera solve` attempts the instruction on its own and the benchmark's
    own tests are the verdict.
    """
    boot = container_bootstrap(wheel, workspace=workspace)
    solve = command_string(
        build_solve_command(instruction, model=model, workspace=workspace, flags=flags, max_attempts=max_attempts)
    )
    return f"{boot} && {solve}"


def _load_base_agent() -> Any:
    """Import terminal_bench's BaseAgent lazily; raise a friendly error if the extra is absent."""
    try:
        from terminal_bench.agents.base_agent import BaseAgent  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "the terminal-bench adapter needs an extra — install with: "
            "pip install 'chimera-agent[bench]'"
        ) from exc
    return BaseAgent


def make_chimera_tb_agent(
    model: str,
    *,
    flags: tuple[str, ...] = _DEFAULT_FLAGS,
    wheel: str | None = None,
    max_attempts: int = 3,
) -> Any:
    """Build a ``ChimeraTBAgent`` class bound to a model (Harbor imports/instantiates it).

    Kept as a factory so the ``terminal_bench`` base class is only touched when actually running
    the benchmark — the module stays importable (and testable) without the extra. When ``wheel`` is
    given (a container-side path to a self-built chimera wheel), the agent installs it before running
    ``chimera solve`` — the only way the CLI exists inside a minimal task container.
    """
    base = _load_base_agent()

    class ChimeraTBAgent(base):  # type: ignore[misc, valid-type]
        """Drives each Terminal-Bench task by running ``chimera solve`` in the task container."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._model = model
            self._flags = flags
            self._wheel = wheel
            self._max_attempts = max_attempts

        @staticmethod
        def name() -> str:
            return "chimera"

        def perform_task(self, instruction: str, session: Any, *args: Any, **kwargs: Any) -> Any:
            if self._wheel:
                command = build_container_command(
                    instruction, model=self._model, wheel=self._wheel,
                    flags=self._flags, max_attempts=self._max_attempts,
                )
            else:
                command = command_string(
                    build_solve_command(
                        instruction, model=self._model, flags=self._flags, max_attempts=self._max_attempts
                    )
                )
            # Harbor's session executes commands inside the task's container and grades with the
            # task's own tests afterward — we only issue the command.
            return session.send_command(command)

    return ChimeraTBAgent
