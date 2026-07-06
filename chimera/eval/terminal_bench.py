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


def make_chimera_tb_agent(model: str, *, flags: tuple[str, ...] = _DEFAULT_FLAGS) -> Any:
    """Build a ``ChimeraTBAgent`` class bound to a model (Harbor imports/instantiates it).

    Kept as a factory so the ``terminal_bench`` base class is only touched when actually running
    the benchmark — the module stays importable (and testable) without the extra.
    """
    base = _load_base_agent()

    class ChimeraTBAgent(base):  # type: ignore[misc, valid-type]
        """Drives each Terminal-Bench task by running ``chimera solve`` in the task container."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._model = model
            self._flags = flags

        @staticmethod
        def name() -> str:
            return "chimera"

        def perform_task(self, instruction: str, session: Any, *args: Any, **kwargs: Any) -> Any:
            argv = build_solve_command(instruction, model=self._model, flags=self._flags)
            # Harbor's session executes commands inside the task's container and grades with the
            # task's own tests afterward — we only issue the command.
            return session.send_command(command_string(argv))

    return ChimeraTBAgent
