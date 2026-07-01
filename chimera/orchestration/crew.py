"""Crew orchestration — sequential pipelines and supervisor/worker swarms.

Two coordination patterns over role agents:

* :class:`SequentialCrew` — roles run in order, each seeing the *consolidated* prior
  outputs and (optionally) writing to a shared memory.
* :class:`SupervisorCrew` — workers address the task in parallel, their outputs are
  consolidated, and a supervisor synthesizes the final answer.
* :class:`IsolatedCrew` — tool-using workers each edit the SAME task in their OWN git
  worktree in parallel; non-conflicting edits merge back and cross-worker conflicts are
  reported. Composes tool-using roles + worktree isolation + distilled results.

``parallel_review`` runs several reviewers concurrently (CAPRA-style verification).
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from chimera.memory.manager import MemoryManager
from chimera.orchestration.comms import AgentMessage, consolidate, render
from chimera.orchestration.isolation import run_isolated
from chimera.orchestration.roles import Role, RoleAgent
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger
from chimera.tools.registry import ToolRegistry

_log = get_logger("orchestration.crew")


@dataclass
class CrewResult:
    answer: str
    transcript: list[AgentMessage] = field(default_factory=list)


class SequentialCrew:
    """Runs role agents in order; each sees the consolidated prior outputs."""

    def __init__(self, agents: list[RoleAgent], *, shared_memory: MemoryManager | None = None) -> None:
        self.agents = agents
        self.shared_memory = shared_memory

    def run(self, task: str) -> CrewResult:
        transcript: list[AgentMessage] = []
        for agent in self.agents:
            context = render(consolidate(transcript))
            output = agent.act(task, context=context)
            transcript.append(AgentMessage(agent.name, output))
            if self.shared_memory is not None:
                self.shared_memory.remember(output, kind="episodic", key=f"crew:{agent.name}")
        answer = transcript[-1].content if transcript else ""
        return CrewResult(answer=answer, transcript=transcript)


def parallel_review(reviewers: list[RoleAgent], subject: str, *, max_workers: int = 4) -> list[AgentMessage]:
    """Run several reviewers concurrently over the same subject."""
    if not reviewers:
        return []

    def review(agent: RoleAgent) -> AgentMessage:
        return AgentMessage(agent.name, agent.act(subject))

    with ThreadPoolExecutor(max_workers=min(max_workers, len(reviewers))) as pool:
        return list(pool.map(review, reviewers))


class SupervisorCrew:
    """Workers address the task in parallel; a supervisor synthesizes the result."""

    def __init__(
        self,
        supervisor: RoleAgent,
        workers: list[RoleAgent],
        *,
        max_workers: int = 4,
        shared_memory: MemoryManager | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.workers = workers
        self.max_workers = max_workers
        self.shared_memory = shared_memory

    def run(self, task: str) -> CrewResult:
        results = parallel_review(self.workers, task, max_workers=self.max_workers)
        consolidated = consolidate(results)
        if self.shared_memory is not None:
            for message in consolidated:
                self.shared_memory.remember(message.content, kind="episodic", key=f"crew:{message.sender}")
        final = self.supervisor.act(
            f"Synthesize the team's work into a single best answer for the task:\n{task}",
            context=render(consolidated),
        )
        return CrewResult(answer=final, transcript=[*results, AgentMessage(self.supervisor.name, final)])


def demo_crew(backend: SupportsComplete) -> SequentialCrew:
    """A small illustrative research crew: researcher -> critic -> writer."""
    roles = [
        Role("researcher", "You research the task and list the key facts and considerations concisely."),
        Role("critic", "You critique the prior notes, flag gaps and risks, and suggest improvements."),
        Role("writer", "You write the final, polished answer using the prior notes and critique."),
    ]
    return SequentialCrew([RoleAgent(role, backend) for role in roles])


@dataclass
class IsolatedWorker:
    """A tool-using worker for an :class:`IsolatedCrew`.

    ``tools`` is a factory: it builds the worker's tool registry rooted at the *isolated*
    workspace it is handed (its own worktree), so its file edits stay contained until merge.
    ``backend`` overrides the crew backend for this worker (else the crew's is used).
    """

    role: Role
    tools: Callable[[Path], ToolRegistry]
    backend: SupportsComplete | None = None
    max_steps: int = 6


@dataclass
class IsolatedCrewResult:
    """Outcome of an isolated crew run — answers, merged edits, and cross-worker conflicts."""

    transcript: list[AgentMessage] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    merged: int = 0
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.conflicts


class IsolatedCrew:
    """Tool-using workers each tackle the task in their own git worktree, in parallel.

    Every worker runs a real agent loop (via a tool-using :class:`RoleAgent`) against an
    isolated checkout, so concurrent edits never collide mid-flight. On merge-back, a file
    two successful workers both changed is a conflict: it is left out and reported rather than
    silently clobbered (mechanical one-file-one-owner). A worker that crashes fails its own
    unit, not the run. Outside a git repo, workers run in-place (no isolation).
    """

    def __init__(
        self,
        backend: SupportsComplete,
        workers: list[IsolatedWorker],
        *,
        max_workers: int = 4,
    ) -> None:
        self.backend = backend
        self.workers = workers
        self.max_workers = max_workers

    def run(
        self,
        task: str,
        workspace: Path,
        *,
        succeeded: Callable[[str], bool] | None = None,
        timeout: float | None = None,
    ) -> IsolatedCrewResult:
        def make_unit(worker: IsolatedWorker) -> Callable[[Path], str]:
            def run_worker(ws: Path) -> str:
                agent = RoleAgent(
                    worker.role,
                    worker.backend or self.backend,
                    tools=worker.tools(ws),
                    max_steps=worker.max_steps,
                )
                return agent.act(task)

            return run_worker

        units = [(w.role.name, make_unit(w)) for w in self.workers]
        batch = run_isolated(
            Path(workspace),
            units,
            succeeded=succeeded or (lambda _: True),
            max_workers=self.max_workers,
            timeout=timeout,
        )
        transcript: list[AgentMessage] = []
        failures: dict[str, str] = {}
        for result in batch.results:
            if result.ok:
                transcript.append(AgentMessage(result.name, result.value or ""))
            else:
                failures[result.name] = result.error
        _log.debug(
            "isolated crew: %d ok, %d failed, %d merged, %d conflict(s)",
            len(transcript), len(failures), batch.merged, len(batch.conflicts),
        )
        return IsolatedCrewResult(
            transcript=transcript, conflicts=batch.conflicts, merged=batch.merged, failures=failures
        )
