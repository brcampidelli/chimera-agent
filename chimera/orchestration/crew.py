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
from typing import Any

from chimera.core.worktree import is_git_repo
from chimera.memory.manager import MemoryManager
from chimera.orchestration.comms import AgentMessage, consolidate, render
from chimera.orchestration.events import OrchEvent, OrchEventSink
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
        # One reviewer failing (e.g. a transient provider error inside its agent loop) must fail only
        # ITS unit, not sink the whole panel — Executor.map would otherwise re-raise the first
        # exception and discard every other reviewer's completed work.
        try:
            return AgentMessage(agent.name, agent.act(subject))
        except Exception as exc:  # noqa: BLE001 — degrade to N-1 reviewers, never crash the run
            return AgentMessage(agent.name, f"[error] {exc}")

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
        if self.shared_memory is not None:
            # Dedup only for MEMORY storage (avoid persisting N near-identical notes).
            for message in consolidate(results):
                self.shared_memory.remember(message.content, kind="episodic", key=f"crew:{message.sender}")
        final = self.supervisor.act(
            f"Synthesize the team's work into a single best answer for the task:\n{task}",
            # Feed the supervisor the RAW reviews, not consolidated ones: collapsing agreeing workers
            # into a single voice would turn a 3-to-1 majority into a 1-to-1 tie before the deciding
            # agent ever sees the real consensus strength.
            context=render(results),
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
class WorkerOutcome:
    """What one isolated worker produced — its answer and whether it passed verification."""

    answer: str
    verified: bool = True


@dataclass
class IsolatedCrewResult:
    """Outcome of an isolated crew run — answers, merged edits, and cross-worker conflicts."""

    transcript: list[AgentMessage] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    merged: int = 0
    failures: dict[str, str] = field(default_factory=dict)  # worker crashed
    rejected: dict[str, str] = field(default_factory=dict)  # ran but failed verification -> not merged
    summary: str = ""  # supervisor's unified report (empty unless a supervisor is set)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.rejected and not self.conflicts


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
        supervisor: RoleAgent | None = None,
        max_workers: int = 4,
        on_event: OrchEventSink | None = None,
        should_stop: Callable[[], bool] | None = None,
        identity: str = "",
    ) -> None:
        self.backend = backend
        self.workers = workers
        # The owner's rendered instructions, handed to every worker. Keyword-only with an empty
        # default so no existing caller changes.
        self.identity = identity
        self.supervisor = supervisor
        self.max_workers = max_workers
        # Same seam, same contract, same reasons as the hierarchy's — see
        # `chimera.orchestration.events`. Until now this class emitted nothing at all, which was
        # fine while its only caller was a CLI printing at the end and is not fine behind a
        # screen: a crew's workers take minutes each, and a run that reports only its final
        # tally is a run nobody can watch.
        #
        # The sink is called from the worker threads `run_isolated` spawns, so it must be
        # thread-safe; the SSE bridge is, and `thread_safe_sink` exists for the ones that are not.
        self.on_event = on_event
        # Checked before a worker starts and before its verify command runs. A crew worker that
        # stops is NOT merged — `succeeded=lambda o: o.verified` sees an unverified outcome — so
        # cancelling discards that worker's worktree rather than landing half an edit.
        self.should_stop = should_stop

    def _emit(self, kind: str, *, text: str = "", task_id: str = "", **data: Any) -> None:
        """Advisory, and never fatal: a consumer that went away must not fail a worker whose
        tokens are already spent."""
        if self.on_event is None:
            return
        try:
            self.on_event(OrchEvent(kind, text, task_id, dict(data)))  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 -- see the docstring
            _log.debug("crew event sink raised (ignored): %s", exc)

    def run(
        self,
        task: str,
        workspace: Path,
        *,
        verify: str | None = None,
        timeout: float | None = None,
    ) -> IsolatedCrewResult:
        """Run the workers in parallel-isolated worktrees; merge only the verified ones.

        ``verify`` is a shell command run in each worker's own worktree after it finishes
        (exit 0 == pass). A worker whose changes fail verification is *rejected* — its edits
        are discarded, not merged — so a broken change never lands. With no ``verify``, every
        worker that didn't crash merges (subject to conflict detection).
        """

        def make_unit(worker: IsolatedWorker) -> Callable[[Path], WorkerOutcome]:
            def run_worker(ws: Path) -> WorkerOutcome:
                name = worker.role.name
                if self.should_stop is not None and self.should_stop():
                    # Unverified, so `succeeded` is False and this worker's worktree is discarded
                    # rather than merged. Stopping must not land half an edit.
                    self._emit("worker_rejected", task_id=name, reason="cancelled",
                               text="cancelled before it started")
                    return WorkerOutcome(answer="", verified=False)
                self._emit(
                    "worker_started", task_id=name, text=worker.role.name,
                    # The branch this worker writes on. `run_isolated` names it, and until now it
                    # was invisible: a user watching parallel edits could not tell which checkout
                    # produced which change, or go look at one afterwards.
                    workspace=str(ws), instruction=worker.role.system_prompt[:400],
                )
                answer = agent_answer = ""
                try:
                    agent = RoleAgent(
                        worker.role,
                        worker.backend or self.backend,
                        tools=worker.tools(ws),
                        max_steps=worker.max_steps,
                        identity=self.identity,
                        # The worktree, not the original: a worker reads the conventions of the
                        # checkout it is actually editing.
                        project_root=ws,
                    )
                    answer = agent_answer = agent.act(task)
                except Exception as exc:  # noqa: BLE001 -- one worker crashing is its own failure
                    self._emit("worker_failed", task_id=name, text=str(exc)[:300])
                    raise
                if not verify:
                    # No verify command: every worker that did not crash merges, subject to the
                    # one-file-one-owner conflict rule. Said out loud because it is the case where
                    # two workers editing the same file BOTH lose.
                    self._emit("worker_verified", task_id=name, verified_by="", answer_chars=len(answer))
                    return WorkerOutcome(answer=answer, verified=True)
                if self.should_stop is not None and self.should_stop():
                    self._emit("worker_rejected", task_id=name, reason="cancelled",
                               text="cancelled before its check ran")
                    return WorkerOutcome(answer=agent_answer, verified=False)
                from chimera.core.verify import CommandVerifier

                outcome = CommandVerifier(verify, ws).verify()
                if outcome.passed:
                    self._emit("worker_verified", task_id=name, verified_by=verify,
                               answer_chars=len(answer))
                else:
                    # The output is what tells you WHY it was thrown away, and a crew whose
                    # workers all fail the same check is a crew whose check is wrong.
                    self._emit("worker_rejected", task_id=name, reason="verify",
                               text=verify, detail=(outcome.output or "")[:2000])
                return WorkerOutcome(answer=answer, verified=outcome.passed)

            return run_worker

        units = [(w.role.name, make_unit(w)) for w in self.workers]
        batch = run_isolated(
            Path(workspace),
            units,
            succeeded=lambda outcome: outcome.verified,  # merge only verified workers
            max_workers=self.max_workers,
            timeout=timeout,
        )
        transcript: list[AgentMessage] = []
        failures: dict[str, str] = {}
        rejected: dict[str, str] = {}
        for result in batch.results:
            if result.error:  # crashed before producing an outcome
                failures[result.name] = result.error
            elif result.value is not None and result.value.verified:  # verified -> merged
                transcript.append(AgentMessage(result.name, result.value.answer))
            else:  # ran but failed verification -> changes discarded
                rejected[result.name] = result.value.answer if result.value is not None else ""
        _log.debug(
            "isolated crew: %d merged, %d rejected, %d failed, %d conflict(s)",
            len(transcript), len(rejected), len(failures), len(batch.conflicts),
        )
        crew_result = IsolatedCrewResult(
            transcript=transcript,
            conflicts=batch.conflicts,
            merged=batch.merged,
            failures=failures,
            rejected=rejected,
        )
        # What each worker actually produced, emitted here and not from inside `run_worker`,
        # because here is where it exists: `run_isolated` reads each worktree's changed files as
        # it collects, which is the last moment before the worktree is removed. Asking mid-flight
        # would mean staging a checkout the worker is still writing to.
        #
        # Emitted for the rejected and the failed ones too, and that is the point. A worker whose
        # attempt was thrown away leaves nothing else behind — without this, the only account of
        # a discarded attempt is that it happened.
        contested = set(batch.conflicts)
        for result in batch.results:
            answer = result.value.answer if result.value is not None else ""
            # Split, because passing the check and landing are DIFFERENT THINGS and conflating
            # them is the exact dishonesty this screen exists to avoid: two workers who both pass
            # on one file both lose it, so a card saying "the files it wrote, and that landed"
            # sat directly above a panel saying nothing landed. `ok` is the verifier's verdict;
            # only `changed - conflicts` actually reached the workspace.
            landed = [p for p in result.changed_paths if result.ok and p not in contested]
            lost = [p for p in result.changed_paths if not result.ok or p in contested]
            self._emit(
                "worker_produced",
                task_id=result.name,
                files=landed,
                lost=lost,
                # Truncated because a worker's report can be long and this rides the same channel
                # as the progress frames; the merged files themselves are on disk to be read.
                answer=answer[:2000],
                landed=bool(landed),
            )
        # One frame per contested file, not one lump. A conflict is a file two workers both
        # changed and NEITHER landed — the thing a person has to go look at by name.
        for path in batch.conflicts:
            self._emit("conflict", text=path, path=path)
        if self.supervisor is not None:
            self._emit("synthesizing", text=f"{len(transcript)} merged")
            crew_result.summary = self._synthesize(task, crew_result)
        self._emit(
            "done",
            text=f"{batch.merged} merged",
            merged=batch.merged,
            conflicts=list(batch.conflicts),
            failed=sorted(failures),
            rejected=sorted(rejected),
            answer=crew_result.summary,
            # False means the workers ran IN PLACE, sharing one folder, because this is not a git
            # repository. Everything above about isolation stops being true, and the screen has to
            # be able to say so — the same honesty `/api/agents` already ships as `is_repo`.
            # Asked of the workspace rather than read off the batch, which is what `/api/agents`
            # does too: `run_isolated` reports what it produced, not whether it was able to isolate.
            is_repo=is_git_repo(Path(workspace)),
        )
        return crew_result

    def _synthesize(self, task: str, result: IsolatedCrewResult) -> str:
        """Have the supervisor fold the merged workers' outputs into one unified report."""
        assert self.supervisor is not None
        parts: list[str] = []
        if result.transcript:
            parts.append("Merged worker outputs:\n" + render(consolidate(result.transcript)))
        if result.conflicts:
            parts.append("Files in conflict (NOT merged): " + ", ".join(result.conflicts))
        if result.rejected:
            parts.append("Workers rejected by verification: " + ", ".join(result.rejected))
        if result.failures:
            parts.append("Workers that crashed: " + ", ".join(result.failures))
        context = "\n\n".join(parts) if parts else "The team produced no merged output."
        return self.supervisor.act(
            f"Synthesize the team's work into a single unified report for the task:\n{task}",
            context=context,
        )
