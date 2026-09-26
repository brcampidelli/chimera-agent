"""Context Explorer — an isolated repository-exploration subagent (FastContext-style).

Repository exploration eats a large share of a coding agent's context and tokens. This
subagent takes a natural-language query, does its OWN bounded read-only search (glob / grep
/ read_file / list_dir), and returns only a compact ``file:line`` evidence block. Its internal
turns are NOT returned to the caller, so the main agent's context stays clean — the core idea
of FastContext (arXiv 2606.14066): separate *exploration* from *solving*.

No fine-tuning is required (that is the paper's Layer B, a Tier-4 aspiration): the explorer
runs on any backend, ideally a cheap one, since localization is a narrow task where a small
specialised model beats delegating to a frontier model.

**The contract (study 25, S12), behind ``CHIMERA_EXPLORER_CONTRACT``, off by default.** On, the
caller names a thoroughness level, which sets the step ceiling; the explorer reports conclusions
with a ``path:line`` each and a gaps section that separates what it read from what it inferred;
and the harness checks every cited location against the workspace, because the prompt can only
ask for real locations. Off, the explorer sends today's text byte for byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.agent import Agent, AgentConfig, run_nested
from chimera.orchestration.budget import SpendBudget
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger
from chimera.tools.base import Tool
from chimera.tools.files import ListDirTool, ReadFileTool
from chimera.tools.registry import ToolRegistry
from chimera.tools.search import GlobTool, GrepTool
from chimera.tools.workspace import PathEscapesWorkspaceError, resolve_in_workspace

_log = get_logger("core.explorer")

EXPLORER_SYSTEM = (
    "You are a repository EXPLORER subagent. Your only job is to LOCATE the code most "
    "relevant to a query — never to solve the task, edit files, or write code. Use `glob` "
    "and `grep` to find candidates and `read_file` to confirm. Prefer a few broad searches "
    "over many narrow ones. When done, reply with ONLY this block and nothing else:\n"
    "<final_answer>\n"
    "path/to/file.py:START-END (short note on why it is relevant)\n"
    "path/to/other.py:LINE (short note)\n"
    "</final_answer>\n"
    "List the most relevant locations (at most 8), most important first. Line ranges are "
    "optional but preferred. Do not include anything outside the block."
)
_TASK = "Find the code locations most relevant to this query:\n\n{query}"

#: The explorer's contract, sent instead of :data:`EXPLORER_SYSTEM` when
#: ``CHIMERA_EXPLORER_CONTRACT`` is on. Study 25 §7 S12 names five properties: read-only, a
#: thoroughness level, ``path:line`` for every claim, a gaps section separating verified from
#: inferred, and conclusions rather than file dumps. It names no tool, because the registry it runs
#: with is the caller's business, and every rule carries its reason. Unmeasured.
EXPLORER_CONTRACT_SYSTEM = (
    "You explore a code repository for another agent, which is working on a task and needs to know "
    "where things are and how they work. You can list, search and read files. You cannot change "
    "them, and solving the task is not your job, because the other agent will act on what you "
    "report.\n\n"
    "The task gives a thoroughness level. Quick: find the one place that answers the query, then "
    "stop. Medium: confirm it by reading the code, and look at its direct callers. Thorough: also "
    "look for what would contradict your first finding, such as a second implementation, a "
    "setting that overrides it or a test that expects something else, since a question answered "
    "from one file is often answered wrongly.\n\n"
    "Report conclusions, not listings. Each finding is one sentence about what the code does or "
    "where it lives, followed by the location you read it at, as path/to/file.py:40-58. Cite only "
    "lines you opened during this task: the locations are checked against the workspace, and one "
    "that does not exist costs the other agent a wasted step.\n\n"
    "Reply with this block and nothing outside it:\n"
    "<final_answer>\n"
    "Findings:\n"
    "- one sentence, then path:line\n"
    "Gaps:\n"
    "- Verified: what you read in the code itself.\n"
    "- Inferred: what you concluded without reading it, and from what.\n"
    "- Not found: what you looked for and could not locate.\n"
    "</final_answer>"
)
#: The explorer's task under the contract. The level and the step ceiling are stated, so the model
#: can pace itself against the budget the harness actually enforces.
_CONTRACT_TEMPLATE = (
    "Thoroughness: {level}. You have at most {steps} tool steps.\n\n"
    "What to find out:\n{query}"
)

#: The levels a caller may ask for. Medium is the ceiling the caller configured, so a caller that
#: names no level spends exactly what it spent before the levels existed.
THOROUGHNESS: tuple[str, ...] = ("quick", "medium", "thorough")


def thoroughness_steps(level: str, base: int) -> int:
    """The step ceiling for ``level``: half of ``base``, ``base``, or twice ``base``.

    An unknown level reads as medium rather than failing: the value comes from a model's tool call,
    and a misspelt level should cost the setting it missed, not the whole delegation."""
    if level == "quick":
        return max(2, base // 2)
    if level == "thorough":
        return base * 2
    return base


# path, optional :line-range, optional (note). Path is any non-space run that isn't pure prose.
_LINE = re.compile(
    r"^[-*\s]*(?P<path>[\w./\\-]+?)(?::(?P<lines>\d+(?:-\d+)?))?\s*(?:\((?P<note>.*?)\))?\s*$"
)
_BLOCK = re.compile(r"<final_answer>\s*(.*?)\s*</final_answer>", re.DOTALL | re.IGNORECASE)
#: A ``path:line`` or ``path:start-end`` anywhere in a line. The path must end in an extension, so
#: a time of day or a ratio in prose is not read as a location.
_REF = re.compile(r"(?P<path>[\w./\\-]*\w\.[A-Za-z0-9]+):(?P<start>\d+)(?:-(?P<end>\d+))?")


@dataclass(frozen=True)
class Evidence:
    """One located code region."""

    path: str
    lines: str = ""  # "42-58", "42", or ""
    note: str = ""

    def as_line(self) -> str:
        loc = f"{self.path}:{self.lines}" if self.lines else self.path
        return f"{loc} ({self.note})" if self.note else loc


def parse_evidence(answer: str) -> list[Evidence]:
    """Parse an explorer answer into structured evidence (pure, deterministic).

    Reads the ``<final_answer>`` block if present, else the whole text. Lines that don't look
    like a path reference are ignored, so stray prose can't leak in as fake evidence.
    """
    match = _BLOCK.search(answer)
    body = match.group(1) if match else answer
    out: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LINE.match(line)
        if not m:
            continue
        path = m.group("path")
        # A real path reference has an extension or a separator — reject bare prose words.
        if "." not in path and "/" not in path and "\\" not in path:
            continue
        key = (path, m.group("lines") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(Evidence(path=path, lines=m.group("lines") or "", note=(m.group("note") or "").strip()))
    return out


def _report_body(answer: str) -> str:
    """The contract's ``<final_answer>`` body, or the whole answer when the block is missing."""
    match = _BLOCK.search(answer)
    return (match.group(1) if match else answer).strip()


def evidence_from_report(report: str) -> list[Evidence]:
    """The locations a contract report cites, each with the sentence that cites it as its note.

    A finding under the contract is prose followed by a location, which :func:`parse_evidence`
    rightly refuses (it keeps prose out of the older format). This reads the location out of the
    sentence instead, so a caller that lists evidence still gets it."""
    out: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for raw in report.splitlines():
        for m in _REF.finditer(raw):
            lines = m.group("start") + (f"-{m.group('end')}" if m.group("end") else "")
            key = (m.group("path"), lines)
            if key in seen:
                continue
            seen.add(key)
            note = _REF.sub("", raw).strip(" -*\t,;:()[]")
            out.append(Evidence(path=m.group("path"), lines=lines, note=note))
    return out


@dataclass(frozen=True)
class LocationCheck:
    """Which of a report's cited locations exist in the workspace — the harness's half of the
    contract. The prompt asks for real locations; only this can tell whether it got them."""

    cited: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    def receipt(self) -> str:
        """One line for the caller, in the harness's own words, never the explorer's."""
        if not self.cited:
            return "[location check: the report cites no path:line]"
        if not self.missing:
            return f"[location check: all {len(self.cited)} cited locations exist in the workspace]"
        found = len(self.cited) - len(self.missing)
        return (
            f"[location check: {found} of {len(self.cited)} cited locations exist in the "
            f"workspace. Not found, so unverified: {', '.join(self.missing)}]"
        )


def _location_exists(workspace: Path, path: str, start: int, end: int) -> bool:
    """True when ``path`` is a file inside ``workspace`` with at least ``end`` lines."""
    try:
        target = resolve_in_workspace(workspace, path.replace("\\", "/"))
    except (PathEscapesWorkspaceError, OSError):
        return False
    if not target.is_file() or start < 1 or end < start:
        return False
    try:
        with target.open("rb") as handle:
            count = sum(1 for _ in handle)
    except OSError:
        return False
    return end <= count


def check_locations(report: str, workspace: Path) -> LocationCheck:
    """Check every ``path:line`` in ``report`` against ``workspace`` (pure apart from reading)."""
    cited: list[str] = []
    missing: list[str] = []
    for m in _REF.finditer(report):
        ref = m.group(0)
        if ref in cited:
            continue
        cited.append(ref)
        start = int(m.group("start"))
        end = int(m.group("end") or start)
        if not _location_exists(workspace, m.group("path"), start, end):
            missing.append(ref)
    return LocationCheck(tuple(cited), tuple(missing))


@dataclass
class ExplorerResult:
    """What the explorer returns — evidence only, never its internal transcript."""

    query: str
    evidence: list[Evidence] = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    #: Under the contract: the report's body (findings and gaps) and the harness's location check.
    #: Empty and None otherwise, so a caller of the older explorer sees nothing new.
    report: str = ""
    check: LocationCheck | None = None
    #: What the exploration's own run spent, as its `AgentResult` reported it: ``usd`` None when a
    #: call had no price. Kept because the run is somebody's bill, and dropping it made every
    #: exploration free on the receipt of the turn that asked for it.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float | None = 0.0
    model: str = ""

    @property
    def block(self) -> str:
        if not self.evidence:
            return "<final_answer>\n(no relevant locations found)\n</final_answer>"
        body = "\n".join(e.as_line() for e in self.evidence)
        return f"<final_answer>\n{body}\n</final_answer>"

    def as_context(self) -> str:
        """The compact string the main agent receives (no explorer reasoning)."""
        if self.check is not None:
            report = self.report or f"(the explorer reported nothing for: {self.query})"
            return f"{report}\n\n{self.check.receipt()}"
        if not self.evidence:
            return f"No repository locations found for: {self.query}"
        lines = "\n".join(f"- {e.as_line()}" for e in self.evidence)
        return f"Relevant code locations for '{self.query}':\n{lines}"


def read_only_registry(workspace: Path | None = None) -> ToolRegistry:
    """A registry with only the read-only discovery tools an explorer may use."""
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace))
    registry.register(ListDirTool(workspace))
    registry.register(GrepTool(workspace))
    registry.register(GlobTool(workspace))
    return registry


def _contract_default() -> bool:
    from chimera.config import get_settings

    return get_settings().explorer_contract


class ContextExplorer:
    """Runs a bounded, read-only exploration and returns only a ``file:line`` block."""

    def __init__(
        self,
        backend: SupportsComplete,
        workspace: Path | None = None,
        *,
        model: str | None = None,
        max_turns: int = 8,
        contract: bool | None = None,
    ) -> None:
        self.backend = backend
        self.workspace = (workspace or Path.cwd()).resolve()
        self.model = model
        self.max_turns = max_turns
        #: None reads ``CHIMERA_EXPLORER_CONTRACT`` once, here, so one explorer keeps one contract.
        self.contract = _contract_default() if contract is None else contract

    def explore(
        self, query: str, thoroughness: str = "medium", *, spend: SpendBudget | None = None
    ) -> ExplorerResult:
        """Locate code for ``query``. ``spend`` is a ceiling this run draws on and charges, the
        caller's own when the exploration is part of something with one (a turn); a run that
        reaches it stops with what it found so far."""
        if not self.contract:
            return self._explore_plain(query, spend=spend)
        level = thoroughness if thoroughness in THOROUGHNESS else "medium"
        steps = thoroughness_steps(level, self.max_turns)
        agent = Agent(
            self.backend,
            read_only_registry(self.workspace),
            AgentConfig(
                model=self.model,
                max_steps=steps,
                temperature=0.1,
                system_prompt=EXPLORER_CONTRACT_SYSTEM,
            ),
        )
        result = agent.run(
            _CONTRACT_TEMPLATE.format(level=level, steps=steps, query=query), spend=spend
        )
        report = _report_body(result.answer)
        check = check_locations(report, self.workspace)
        _log.debug(
            "explorer (%s) cited %d location(s), %d missing, in %d turn(s)",
            level, len(check.cited), len(check.missing), result.steps,
        )
        return ExplorerResult(
            query=query,
            evidence=evidence_from_report(report),
            turns=result.steps,
            tool_calls=result.tool_calls_made,
            report=report,
            check=check,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            usd=result.usd,
            model=result.model,
        )

    def _explore_plain(self, query: str, *, spend: SpendBudget | None) -> ExplorerResult:
        agent = Agent(
            self.backend,
            read_only_registry(self.workspace),
            AgentConfig(
                model=self.model,
                max_steps=self.max_turns,
                temperature=0.1,
                system_prompt=EXPLORER_SYSTEM,
            ),
        )
        # The transcript stays here, not returned; what the run cost goes with the evidence.
        result = agent.run(_TASK.format(query=query), spend=spend)
        evidence = parse_evidence(result.answer)
        _log.debug("explorer found %d location(s) in %d turn(s)", len(evidence), result.steps)
        return ExplorerResult(
            query=query, evidence=evidence, turns=result.steps, tool_calls=result.tool_calls_made,
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
            usd=result.usd, model=result.model,
        )


#: The ``thoroughness`` argument, offered only under the contract: without it the tool's schema is
#: today's, byte for byte, in every prompt that carries it.
_THOROUGHNESS_PARAMETER: dict[str, Any] = {
    "type": "string",
    "enum": list(THOROUGHNESS),
    "description": (
        "quick: one place that answers it. medium (default): confirmed by reading, with callers. "
        "thorough: also checks for what would contradict it. Deeper costs more steps."
    ),
}


class ExploreRepositoryTool(Tool):
    """A tool that lets the MAIN agent delegate exploration on demand.

    It runs the :class:`ContextExplorer` subagent internally and returns only the compact
    evidence block — the main agent never sees the subagent's search turns, which is exactly
    the context saving FastContext is about.
    """

    name = "explore_repository"
    description = (
        "Delegate repository exploration: given a natural-language query, a subagent searches "
        "the codebase and returns only the most relevant file:line locations. Use this to "
        "locate code without spending your own context on the search."
    )
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to locate in the repo."}},
        "required": ["query"],
    }

    def __init__(
        self,
        backend: SupportsComplete,
        workspace: Path | None = None,
        *,
        model: str | None = None,
        max_turns: int = 8,
        contract: bool | None = None,
    ) -> None:
        self._explorer = ContextExplorer(
            backend, workspace, model=model, max_turns=max_turns, contract=contract
        )
        #: What the last exploration spent. Inside a run it is on that run's bill already; this is
        #: how a caller that ran the tool on its own reads it, having no bill to look at.
        self.last_spend: ExplorerResult | None = None
        if self._explorer.contract:
            # Per instance, so the class attribute (today's schema) is never mutated.
            self.parameters = {
                "type": "object",
                "properties": {
                    **self.parameters["properties"],
                    "thoroughness": _THOROUGHNESS_PARAMETER,
                },
                "required": ["query"],
            }

    def run(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "error: query is required"
        level = str(kwargs.get("thoroughness") or "medium").strip().lower()
        # On the ceiling and the bill of the run this tool was called from, when there is one.
        found = run_nested(
            "the explorer", lambda spend: self._explorer.explore(query, level, spend=spend)
        )
        self.last_spend = found
        return found.as_context()
