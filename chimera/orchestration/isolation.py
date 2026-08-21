"""Isolation for parallel multi-agent work — worktree (filesystem) and process (fault).

At production scale a swarm of agents can't safely share one checkout and one process.
Two orthogonal isolation seams close that gap on a single box (the honest OSS version of
distributed isolation):

* :func:`run_isolated` — filesystem isolation. Each file-mutating unit runs in its **own
  git worktree** (HORIZON-style), so concurrent editors never see each other's half-done
  edits. On merge-back, a file touched by more than one successful unit is a **conflict**:
  it is *not* copied back and is reported instead, rather than silently clobbered — the
  "one file, one owner / write-serialization" discipline enforced mechanically.
* :func:`run_in_processes` — fault + CPU isolation. Self-contained, picklable units run in
  separate OS processes, so one unit crashing, hanging (past ``timeout``) or segfaulting
  yields a failed result instead of taking down the orchestrator. This is the RPC seam:
  only data crosses the boundary, so backends/closures don't (run text/command units, not
  live LLM agents).

Both return the same :class:`IsolatedResult` shape, and both degrade safely: outside a git
repo :func:`run_isolated` runs in-place (documented, no false isolation).
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, TimeoutError, as_completed
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Generic, TypeVar, cast

from chimera.concurrency import Outcome, run_all_with_deadline
from chimera.core.worktree import GitWorktree, is_git_repo
from chimera.telemetry import get_logger

_log = get_logger("orchestration.isolation")

T = TypeVar("T")

# A named unit of work. For run_isolated it takes the (isolated) workspace path and does
# its work there; for run_in_processes it takes no arguments and must be picklable.
IsolatedUnit = tuple[str, Callable[[Path], T]]
ProcessUnit = tuple[str, Callable[[], T]]


@dataclass
class IsolatedResult(Generic[T]):
    """The outcome of one isolated unit."""

    name: str
    ok: bool
    value: T | None = None
    error: str = ""
    changed_paths: list[str] = field(default_factory=list)


@dataclass
class IsolatedBatch(Generic[T]):
    """Results of a parallel isolated run, plus any cross-unit file conflicts."""

    results: list[IsolatedResult[T]]
    conflicts: list[str] = field(default_factory=list)
    merged: int = 0  # changed files copied back to the real workspace

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)


def _batch_deadline(timeout: float | None) -> float | None:
    """The deadline a batch actually runs under: the caller's if it named one, else the deployment's.

    ``None`` used to mean *wait forever*, and every production caller but one left it at ``None`` —
    so the deadline machinery below existed, was tested, and was reached by nobody. One hung worker
    then held its whole batch. Two of the four call sites are served over HTTP — ``POST /api/agents``
    and ``POST /api/kanban/run``, both dispatching on a worker thread behind an SSE stream — so
    there is nobody at a terminal to Ctrl-C them. On the first, the client stayed pinned open *and*
    the endpoint's ``finally`` — the one that pops ``_agents_cancels[batch_id]`` — never ran, so the
    batch leaked for the life of the process.

    Resolving it here rather than at each call site is the whole point: a safe default a caller has
    to remember is the same bug holding a ticket for the next caller. An explicit ``timeout`` still
    wins, and any non-positive value — here or in ``CHIMERA_BATCH_TIMEOUT`` — means *no deadline*.
    ``0`` must never reach :func:`run_all_with_deadline`, where it would expire every unit at once.
    """
    if timeout is not None:
        return timeout if timeout > 0 else None
    from chimera.config import get_settings

    configured = get_settings().batch_timeout
    return configured if configured > 0 else None


def run_isolated(
    workspace: Path,
    units: list[IsolatedUnit[T]],
    *,
    succeeded: Callable[[T], bool] = lambda _: True,
    max_workers: int = 4,
    timeout: float | None = None,
) -> IsolatedBatch[T]:
    """Run each unit in its own git worktree concurrently; merge non-conflicting edits back.

    A file changed by two or more *successful* units is a conflict: it is left out of the
    merge and listed in ``IsolatedBatch.conflicts`` for the caller to resolve. Outside a git
    repo, units run against ``workspace`` directly (no isolation) — safe, but concurrent
    edits are the caller's responsibility there.

    ``timeout`` bounds the WHOLE batch in wall-clock seconds. Omitting it does **not** mean
    *forever* — it means the deployment's ``CHIMERA_BATCH_TIMEOUT`` (see :func:`_batch_deadline`,
    which explains why the default lives there and not in each caller). Pass ``0`` for no bound.
    """
    workspace = Path(workspace).resolve()
    if not units:
        return IsolatedBatch(results=[])
    git = is_git_repo(workspace)
    deadline = _batch_deadline(timeout)

    # Create one worktree per unit up front (in the parent), so each worker only runs its fn.
    trees: dict[str, GitWorktree | None] = {}
    paths: dict[str, Path] = {}
    for name, _ in units:
        tree = GitWorktree.create(workspace) if git else None
        trees[name] = tree
        paths[name] = tree.path if tree is not None else workspace

    results: dict[str, IsolatedResult[T]] = {}
    try:
        outcomes = run_all_with_deadline(
            [(name, partial(fn, paths[name])) for name, fn in units],
            max_workers=max_workers,
            timeout=deadline,
        )
        for name, _ in units:
            results[name] = _collect(outcomes[name], trees[name], succeeded, deadline)
        conflicts, merged = _merge_back(workspace, trees, results)
    finally:
        for tree in trees.values():
            if tree is not None:
                tree.remove()

    return IsolatedBatch(
        results=[results[name] for name, _ in units], conflicts=conflicts, merged=merged
    )


def _collect(
    slot: Outcome[T],
    tree: GitWorktree | None,
    succeeded: Callable[[T], bool],
    timeout: float | None,
) -> IsolatedResult[T]:
    if slot.timed_out:
        return IsolatedResult(slot.name, ok=False, error=f"timed out after {timeout}s")
    if slot.error is not None:
        return IsolatedResult(
            slot.name, ok=False, error=f"{type(slot.error).__name__}: {slot.error}"
        )
    value = cast("T", slot.value)
    ok = bool(succeeded(value))
    # Collected for the REJECTED units too, though only the successful ones are merged. What a
    # unit wrote is the only account of what it tried, and a unit that was thrown away is exactly
    # the one a caller wants that account for — the worktree is about to be removed, so this is
    # the last moment it can be asked. Guarded because a unit that crashed may have left the tree
    # in a state git refuses to read, and a report is never worth failing a batch over.
    changed: list[str] = []
    if tree is not None:
        with suppress(Exception):
            changed = tree.changed_paths()
    return IsolatedResult(slot.name, ok=ok, value=value, changed_paths=changed)


def _merge_back(
    workspace: Path,
    trees: dict[str, GitWorktree | None],
    results: dict[str, IsolatedResult[T]],
) -> tuple[list[str], int]:
    """Copy back each successful unit's non-conflicting edits; return (conflicts, merged)."""
    touched: dict[str, int] = {}
    for result in results.values():
        if result.ok:
            for path in result.changed_paths:
                touched[path] = touched.get(path, 0) + 1
    conflicts = sorted(path for path, count in touched.items() if count > 1)
    conflict_set = set(conflicts)

    merged = 0
    for name, result in results.items():
        tree = trees[name]
        if tree is None or not result.ok:
            continue
        allowed = set(result.changed_paths) - conflict_set
        if allowed:
            merged += tree.copy_back_to(workspace, only=allowed)
    if conflicts:
        _log.debug("isolated run: %d conflicting file(s) not merged: %s", len(conflicts), conflicts)
    return conflicts, merged


def run_in_processes(
    units: list[ProcessUnit[T]],
    *,
    max_workers: int = 4,
    timeout: float | None = None,
) -> list[IsolatedResult[T]]:
    """Run self-contained, picklable units in separate processes (fault + CPU isolation).

    A unit that raises, hangs past ``timeout``, or crashes its worker becomes a failed
    :class:`IsolatedResult` — the orchestrator survives, and a hung unit's *process* is killed
    rather than abandoned. Only data crosses the process boundary, so pass module-level callables
    that return picklable values (the RPC seam), not closures over live backends.

    ``timeout`` resolves exactly as in :func:`run_isolated`: omitted means the deployment's
    ``CHIMERA_BATCH_TIMEOUT``, not forever. Same rule in both siblings on purpose — a reader
    should not have to know which of the two got the fix.
    """
    if not units:
        return []
    deadline = _batch_deadline(timeout)
    out: dict[str, IsolatedResult[T]] = {}
    # As in run_isolated: the deadline belongs on as_completed (the call that waits), not on
    # future.result() of an already-finished future. Without it the documented "hangs past timeout
    # becomes a failed result" was not true — the batch simply blocked forever.
    pool = ProcessPoolExecutor(max_workers=min(max_workers, len(units)))
    try:
        futures = {pool.submit(fn): name for name, fn in units}
        try:
            for future in as_completed(futures, timeout=deadline):
                name = futures[future]
                try:
                    out[name] = IsolatedResult(name, ok=True, value=future.result())
                except Exception as exc:  # noqa: BLE001 — worker crash stays contained
                    out[name] = IsolatedResult(name, ok=False, error=f"{type(exc).__name__}: {exc}")
        except TimeoutError:
            for future, name in futures.items():
                if name in out:
                    continue
                future.cancel()
                out[name] = IsolatedResult(name, ok=False, error=f"timed out after {deadline}s")
    finally:
        # Snapshot the workers BEFORE shutting down: `shutdown` sets the executor's process map to
        # None, so reading it afterwards finds nothing to kill and the straggler survives.
        workers = list((getattr(pool, "_processes", None) or {}).values())
        pool.shutdown(wait=False, cancel_futures=True)
        _kill_stragglers(pool, workers)
    return [out[name] for name, _ in units]


def _kill_stragglers(pool: ProcessPoolExecutor, workers: list[Any]) -> None:
    """Terminate worker processes still running after shutdown.

    ``cancel_futures`` only cancels units that have not STARTED. A unit that is genuinely hung —
    the exact case ``timeout`` exists for — is already running, so it cannot be cancelled and keeps
    running with nobody waiting for it. Two things then go wrong, and only the second is loud:
    a runaway process keeps burning CPU after ``solve-batch`` reported it as failed, and
    ``ProcessPoolExecutor``'s own atexit hook waits for the pool at interpreter shutdown, so the
    program that reported the timeout hangs forever on the way out.

    That second failure is why this is not a tidy-up. It surfaced as the whole test suite passing
    and then never exiting — which reads as "the tests hung", pointing at everything except the
    line responsible. Intermittently, too, since it depends on which thread wins at exit.

    3.14 added ``kill_workers()`` for exactly this; below it there is no public API, so ``workers``
    is a snapshot of the executor's private process map, taken before shutdown emptied it. Every
    access is defensive — this is a best-effort cleanup running in a ``finally``, and it must never
    raise over the real result it is cleaning up after.
    """
    killer = getattr(pool, "kill_workers", None)
    if callable(killer):
        try:
            killer()
            return
        except Exception as exc:  # noqa: BLE001 — fall through to the manual path
            _log.debug("kill_workers failed, terminating individually: %s", exc)
    for proc in workers:
        try:
            if proc.is_alive():
                proc.terminate()
        except Exception as exc:  # noqa: BLE001 — cleanup must not raise over the real result
            _log.debug("could not terminate a straggling worker: %s", exc)
