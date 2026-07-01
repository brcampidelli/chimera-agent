"""Context Explorer — an isolated repository-exploration subagent (FastContext-style).

Repository exploration eats a large share of a coding agent's context and tokens. This
subagent takes a natural-language query, does its OWN bounded read-only search (glob / grep
/ read_file / list_dir), and returns only a compact ``file:line`` evidence block. Its internal
turns are NOT returned to the caller, so the main agent's context stays clean — the core idea
of FastContext (arXiv 2606.14066): separate *exploration* from *solving*.

No fine-tuning is required (that is the paper's Layer B, a Tier-4 aspiration): the explorer
runs on any backend, ideally a cheap one, since localization is a narrow task where a small
specialised model beats delegating to a frontier model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger
from chimera.tools.base import Tool
from chimera.tools.files import ListDirTool, ReadFileTool
from chimera.tools.registry import ToolRegistry
from chimera.tools.search import GlobTool, GrepTool

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

# path, optional :line-range, optional (note). Path is any non-space run that isn't pure prose.
_LINE = re.compile(
    r"^[-*\s]*(?P<path>[\w./\\-]+?)(?::(?P<lines>\d+(?:-\d+)?))?\s*(?:\((?P<note>.*?)\))?\s*$"
)
_BLOCK = re.compile(r"<final_answer>\s*(.*?)\s*</final_answer>", re.DOTALL | re.IGNORECASE)


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


@dataclass
class ExplorerResult:
    """What the explorer returns — evidence only, never its internal transcript."""

    query: str
    evidence: list[Evidence] = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0

    @property
    def block(self) -> str:
        if not self.evidence:
            return "<final_answer>\n(no relevant locations found)\n</final_answer>"
        body = "\n".join(e.as_line() for e in self.evidence)
        return f"<final_answer>\n{body}\n</final_answer>"

    def as_context(self) -> str:
        """The compact string the main agent receives (no explorer reasoning)."""
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


class ContextExplorer:
    """Runs a bounded, read-only exploration and returns only a ``file:line`` block."""

    def __init__(
        self,
        backend: SupportsComplete,
        workspace: Path | None = None,
        *,
        model: str | None = None,
        max_turns: int = 8,
    ) -> None:
        self.backend = backend
        self.workspace = (workspace or Path.cwd()).resolve()
        self.model = model
        self.max_turns = max_turns

    def explore(self, query: str) -> ExplorerResult:
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
        result = agent.run(_TASK.format(query=query))  # the transcript stays here, not returned
        evidence = parse_evidence(result.answer)
        _log.debug("explorer found %d location(s) in %d turn(s)", len(evidence), result.steps)
        return ExplorerResult(
            query=query, evidence=evidence, turns=result.steps, tool_calls=result.tool_calls_made
        )


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
    ) -> None:
        self._explorer = ContextExplorer(backend, workspace, model=model, max_turns=max_turns)

    def run(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "error: query is required"
        return self._explorer.explore(query).as_context()
