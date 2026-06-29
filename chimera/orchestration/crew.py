"""Crew orchestration — sequential pipelines and supervisor/worker swarms.

Two coordination patterns over role agents:

* :class:`SequentialCrew` — roles run in order, each seeing the *consolidated* prior
  outputs and (optionally) writing to a shared memory.
* :class:`SupervisorCrew` — workers address the task in parallel, their outputs are
  consolidated, and a supervisor synthesizes the final answer.

``parallel_review`` runs several reviewers concurrently (CAPRA-style verification).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from chimera.memory.manager import MemoryManager
from chimera.orchestration.comms import AgentMessage, consolidate, render
from chimera.orchestration.roles import Role, RoleAgent
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger

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
