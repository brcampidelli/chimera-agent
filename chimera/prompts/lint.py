"""A report on the registered prompts: what the plan's composition rules would flag today.

Study 25 (`bench/PLAN-study25-system-prompts.md` §5.3) set the rules the prompt stack should keep.
This reports how far the current text is from them. It **reports only**. Nothing fails on it yet: a
lint that fails on day one is switched off on day two, and wave 1 of the plan is what brings the
numbers down. Each check names the rule it serves:

- **fences**: every distinct ``<<marker`` the prompts use. Rule 2 asks for one fence syntax, and a
  model told about one marker cannot be expected to recognise five.
- **shouting**: words in capitals per thousand words. Rule 7 says "at normal volume, with the
  reason"; the literature and the vendors' own audit guides find that capitals over-trigger on newer
  models.
- **language rules**: every sentence that tells the model which language to answer in. Rule 3 asks
  for one, in one place.
- **tools in prose**: a tool named in prompt text. Rule 6 says to name a tool only where it exists;
  a sentence about a tool the session was not given invites a call to nothing.
- **length**: sections over a word budget. The plan budgets each layer.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from chimera.prompts.registry import SECTIONS

#: Capitalised words that are names, not emphasis: formats, protocols, labels a model must emit.
_NOT_SHOUTING = frozenset({
    "JSON", "API", "URL", "URLS", "CLI", "SQL", "HTTP", "HTTPS", "ID", "IDS", "OK", "AGENTS", "README",
    "BLOCK", "REVIEW", "ALLOW", "PASS", "FAIL", "YES", "NO", "TODO", "PR", "CSV", "HTML", "CSS", "UI",
    "UTC", "SHA", "LLM", "AI", "MCP", "TSV", "YAML", "DROPPED", "KEPT", "DONE", "STATUS", "COMPLETE",
    "ASCII", "UTF", "PDF", "CI", "OS", "IO", "ANSWER", "PNG", "TRS", "START", "END", "LINE",
})
_CAPS = re.compile(r"\b[A-Z][A-Z]{2,}\b")
#: Lower case on purpose: every fence is spelled that way, and an edit format's ``<<<<<<< SEARCH``
#: marker is not a fence.
_FENCE = re.compile(r"<<\s*(?!end\b)([a-z][a-z0-9-]*)")
_LANGUAGE = re.compile(
    r"[^.\n]*\b(?:answer|reply|write|respond)\b[^.\n]*\blanguage\b[^.\n]*[.\n]"
    r"|[^.\n]*\blanguage\b[^.\n]*\b(?:answer|reply|write|respond)\b[^.\n]*[.\n]",
    re.IGNORECASE,
)
#: The words per section the plan budgets for the largest layers (L0 and each L1 module).
WORD_BUDGET = 250


@dataclass(frozen=True)
class Finding:
    rule: str
    section: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    fences: dict[str, list[str]] = field(default_factory=dict)
    #: The same count over every string in the package (see :func:`source_fences`).
    source_fences: dict[str, list[str]] = field(default_factory=dict)

    def by_rule(self, rule: str) -> list[Finding]:
        return [f for f in self.findings if f.rule == rule]


def tool_names(root: Path | None = None) -> set[str]:
    """Every ``name = "..."`` a Tool class declares under ``chimera/tools``, read from source."""
    base = root or Path(__file__).resolve().parent.parent
    names: set[str] = set()
    for path in sorted((base / "tools").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "name"
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                    and "_" in stmt.value.value  # one-word names ("echo", "shell") are ordinary words
                ):
                    names.add(stmt.value.value)
    return names


def source_fences(root: Path | None = None) -> dict[str, list[str]]:
    """Every ``<<marker`` written in a string anywhere under ``chimera/``, with the files using it.

    The registry snapshots constant prompts only. Several fences live in text composed at runtime
    (a quarantine template, a manager's evidence block), so counting fences in snapshots alone would
    miss exactly the ones the study found scattered.
    """
    base = root or Path(__file__).resolve().parent.parent
    found: dict[str, set[str]] = {}
    for path in sorted(base.rglob("*.py")):
        if path.resolve() == Path(__file__).resolve():
            continue  # this module names fences in order to describe them
        rel = path.relative_to(base.parent).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "<<" in node.value:
                for marker in _FENCE.finditer(node.value):
                    found.setdefault(marker.group(1).lower(), set()).add(rel)
    return {marker: sorted(files) for marker, files in sorted(found.items())}


def lint(texts: dict[str, str], tools: set[str] | None = None) -> Report:
    """Check ``{section id: text}`` against the composition rules. Pure: no registry, no files."""
    report = Report()
    for section, text in sorted(texts.items()):
        for marker in sorted({m.group(1).lower() for m in _FENCE.finditer(text)}):
            report.fences.setdefault(marker, []).append(section)
        words = len(text.split())
        shouted = [w for w in _CAPS.findall(text) if w not in _NOT_SHOUTING]
        if words and shouted:
            per_k = 1000 * len(shouted) / words
            report.findings.append(
                Finding("shouting", section, f"{len(shouted)} in {words} words ({per_k:.1f}/1k): "
                        + ", ".join(sorted(set(shouted))[:8]))
            )
        for match in _LANGUAGE.finditer(text):
            report.findings.append(Finding("language rule", section, match.group(0).strip()[:160]))
        for name in sorted(tools or set()):
            if re.search(rf"\b{re.escape(name)}\b", text):
                report.findings.append(Finding("tool in prose", section, name))
        if words > WORD_BUDGET:
            report.findings.append(Finding("length", section, f"{words} words (budget {WORD_BUDGET})"))
    if len(report.fences) > 1:
        report.findings.append(
            Finding("fences", "*", f"{len(report.fences)} fence syntaxes: " + ", ".join(sorted(report.fences)))
        )
    return report


def lint_registry() -> Report:
    """The report for every registered prompt that has fixed text."""
    texts = {s.id: t for s in SECTIONS if (t := s.text()) is not None}
    report = lint(texts, tool_names())
    in_source = source_fences()
    if len(in_source) > 1:
        report.findings.append(
            Finding("fences", "source", f"{len(in_source)} fence syntaxes in source: " + ", ".join(in_source))
        )
    report.source_fences = in_source
    return report
