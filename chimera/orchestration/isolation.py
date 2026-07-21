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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, TimeoutError, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, TypeVar

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
    """
    workspace = Path(workspace).resolve()
    if not units:
        return IsolatedBatch(results=[])
    git = is_git_repo(workspace)

    # Create one worktree per unit up front (in the parent), so each worker only runs its fn.
    trees: dict[str, GitWorktree | None] = {}
    paths: dict[str, Path] = {}
    for name, _ in units:
        tree = GitWorktree.create(workspace) if git else None
        trees[name] = tree
        paths[name] = tree.path if tree is not None else workspace

    results: dict[str, IsolatedResult[T]] = {}
    try:
        # `timeout` is a deadline for the WHOLE batch, not per unit: it must be passed to
        # as_completed, which is what actually waits. Passing it only to future.result() (the old
        # code) bounded nothing — as_completed yields futures that have ALREADY finished, so its
        # result() never waits and the TimeoutError branch was unreachable. A single hung unit
        # blocked the iteration forever, which is precisely what this parameter exists to prevent.
        pool = ThreadPoolExecutor(max_workers=min(max_workers, len(units)))
        try:
            futures = {pool.submit(fn, paths[name]): name for name, fn in units}
            try:
                for future in as_completed(futures, timeout=timeout):
                    name = futures[future]
                    results[name] = _collect(name, future, trees[name], succeeded, timeout)
            except TimeoutError:
                for future, name in futures.items():
                    if name in results:
                        continue
                    future.cancel()  # only helps if it has not started
                    results[name] = IsolatedResult(name, ok=False, error=f"timed out after {timeout}s")
        finally:
            # Do NOT wait here. Python cannot kill a running thread, so waiting on a hung unit is
            # the very hang the timeout exists to prevent — the batch would return late or never.
            # Queued units are cancelled; a unit already running is abandoned to die with the
            # process, and its slot is reported as a timeout above.
            pool.shutdown(wait=False, cancel_futures=True)
        conflicts, merged = _merge_back(workspace, trees, results)
    finally:
        for tree in trees.values():
            if tree is not None:
                tree.remove()

    return IsolatedBatch(
        results=[results[name] for name, _ in units], conflicts=conflicts, merged=merged
    )


def _collect(
    name: str,
    future: object,
    tree: GitWorktree | None,
    succeeded: Callable[[T], bool],
    timeout: float | None,
) -> IsolatedResult[T]:
    try:
        value: T = future.result(timeout=timeout)  # type: ignore[attr-defined]
    except TimeoutError:
        return IsolatedResult(name, ok=False, error=f"timed out after {timeout}s")
    except Exception as exc:  # noqa: BLE001 — a crashing unit must not fail the batch
        return IsolatedResult(name, ok=False, error=f"{type(exc).__name__}: {exc}")
    ok = bool(succeeded(value))
    changed = tree.changed_paths() if (tree is not None and ok) else []
    return IsolatedResult(name, ok=ok, value=value, changed_paths=changed)


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
    :class:`IsolatedResult` — the orchestrator survives. Only data crosses the process
    boundary, so pass module-level callables that return picklable values (the RPC seam),
    not closures over live backends.
    """
    if not units:
        return []
    out: dict[str, IsolatedResult[T]] = {}
    # As in run_isolated: the deadline belongs on as_completed (the call that waits), not on
    # future.result() of an already-finished future. Without it the documented "hangs past timeout
    # becomes a failed result" was not true — the batch simply blocked forever.
    pool = ProcessPoolExecutor(max_workers=min(max_workers, len(units)))
    try:
        futures = {pool.submit(fn): name for name, fn in units}
        try:
            for future in as_completed(futures, timeout=timeout):
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
                out[name] = IsolatedResult(name, ok=False, error=f"timed out after {timeout}s")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return [out[name] for name, _ in units]
