"""Maturity scorecard (M15-B5) — a self-eval spine over Chimera's surfaces.

OpenClaw scores its own maturity with a taxonomy of surfaces × coverage-IDs tied to QA evidence, so
"is this done?" becomes an auditable rubric instead of a vibe. This is the Chimera version: each
surface (fusion, evolution, governance, memory, benchmarks, resilience, interop) declares the
capabilities that define it, and each capability's evidence is a named test file — counted as
"proven" iff that file exists. The result is machine-derived (glob the tests dir), and doubles
as a per-surface objective function for the evolution loop: the weakest surface / the missing
coverage-IDs are exactly what to shore up next.

What it counts, and only that: for each coverage-ID, whether a test FILE with the named stem exists.
Not whether it passes, not whether it tests the capability, not any bench outcome. A renamed or
deleted test shows up as a gap, which is the drift this is good for.

The bands were once called Alpha / Beta / GA. "GA" reads as "generally available", a claim about the
product that a glob of file names cannot carry, and it was what the desktop Maturity screen showed for
every surface. The bands now name what was counted: ``present`` (>=90% of the surface's coverage-IDs
have their test file), ``partial`` (>=50%), ``sparse`` (below). Whether a surface actually delivers is
read in ``bench/*/RESULTS.md``, not here (study 30, S30-15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Coverage:
    """One capability that defines a surface, proven by a named test file's presence."""

    id: str
    description: str
    evidence: str  # a test-file stem (e.g. "test_diff_gate"); only its presence is checked


@dataclass
class Surface:
    """A product surface and the coverage-IDs that constitute it (AND-ed)."""

    name: str
    coverage: list[Coverage] = field(default_factory=list)


def _band(ratio: float) -> str:
    """The share of coverage-IDs whose test file exists, as a word. Not a release grade."""
    return "present" if ratio >= 0.9 else "partial" if ratio >= 0.5 else "sparse"


@dataclass
class SurfaceScore:
    """How much of a surface is proven, and what is missing."""

    name: str
    proven: int
    total: int
    missing: list[str]

    @property
    def ratio(self) -> float:
        return self.proven / self.total if self.total else 0.0

    @property
    def level(self) -> str:
        """present (>=90%) / partial (>=50%) / sparse — a band over test-file presence, not a grade."""
        return _band(self.ratio)


@dataclass
class Scorecard:
    """The whole-project maturity read: per-surface scores + the weakest surface to target next."""

    surfaces: list[SurfaceScore]

    @property
    def proven(self) -> int:
        return sum(s.proven for s in self.surfaces)

    @property
    def total(self) -> int:
        return sum(s.total for s in self.surfaces)

    @property
    def ratio(self) -> float:
        return self.proven / self.total if self.total else 0.0

    @property
    def level(self) -> str:
        return _band(self.ratio)

    def weakest(self) -> SurfaceScore | None:
        """The surface with the lowest coverage — the evolution loop's next objective."""
        incomplete = [s for s in self.surfaces if s.missing]
        return min(incomplete, key=lambda s: s.ratio) if incomplete else None


# The taxonomy: surfaces × coverage-IDs, each tied to a real test file. Renaming a test surfaces as a
# gap here on purpose — the scorecard tracks drift, it does not paper over it.
CHIMERA_TAXONOMY: list[Surface] = [
    Surface("fusion", [
        Coverage("fusion.panel_judge_synth", "panel -> judge -> synthesizer", "test_fusion"),
        Coverage("fusion.cost_receipts", "per-advisor cost receipts", "test_receipts"),
        Coverage("fusion.selective_router", "agreement-based escalation", "test_agreement_routing"),
        Coverage("fusion.self_consistency", "best-of-N cheap fusion", "test_self_consistency"),
        Coverage("fusion.verifier_select", "verifier-select (Weaver-lite)", "test_verifier_select"),
    ]),
    Surface("evolution", [
        Coverage("evolution.diff_gate", "diff-gated acceptance", "test_diff_gate"),
        Coverage("evolution.distill_correction", "failed->passed distillation", "test_distill_correction"),
        Coverage("evolution.rft_gate", "A/B-gated RFT loop", "test_rft_loop"),
        Coverage("evolution.gepa", "Pareto prompt evolution", "test_gepa"),
        Coverage("evolution.playbook", "ACE delta-playbook", "test_playbook"),
        Coverage("evolution.stagnation", "anti-stagnation signal", "test_stagnation"),
        Coverage("evolution.skill_lifecycle", "skill retire/reactivate", "test_lifecycle"),
        Coverage("evolution.skill_md", "SKILL.md interop", "test_skill_md"),
    ]),
    Surface("governance", [
        Coverage("governance.taint_provenance", "taint lineage on artifacts", "test_taint_provenance"),
        Coverage("governance.sanitize", "control-token stripping", "test_sanitize"),
        Coverage("governance.idempotency", "side-effect idempotency", "test_idempotency"),
        Coverage("governance.quarantine", "dual-LLM quarantined reader", "test_quarantine"),
        Coverage("governance.allowlist", "taint-adaptive allowlist", "test_allowlist"),
        Coverage("governance.injection_redteam", "injection red-team metric", "test_injection"),
        Coverage("governance.validator", "constrained edit validator", "test_governance"),
    ]),
    Surface("memory", [
        Coverage("memory.layers", "layered memory + manager", "test_memory"),
        Coverage("memory.bench", "recall quality under growth", "test_memory_bench"),
        Coverage("memory.semantic", "semantic retrieval opt-in", "test_semantic_memory"),
        Coverage("memory.value_gate", "value-gated writes", "test_memory_value"),
    ]),
    Surface("benchmarks", [
        Coverage("bench.honest_ab", "Wilson/Newcombe A/B", "test_bench_ab"),
        Coverage("bench.paired", "paired McNemar A/B", "test_paired"),
        Coverage("bench.swe", "SWE-bench adapter", "test_swe_bench"),
        Coverage("bench.rubric", "authorable rubric grading", "test_rubric_grade"),
        Coverage("bench.continuous", "continuous-evolution bench", "test_eval_continuous"),
    ]),
    Surface("resilience", [
        Coverage("resilience.checkpoint_resume", "durable resume by thread", "test_checkpoint_resume"),
        Coverage("resilience.fork_paired", "checkpoint fork", "test_paired"),
        Coverage("resilience.tool_loop", "tool-loop circuit breaker", "test_tool_loop"),
        Coverage("resilience.hitl", "HITL accept/edit/respond/ignore", "test_hitl_envelope"),
        Coverage("resilience.contract", "completion contracts", "test_contract"),
        Coverage("resilience.strong_verify", "gated strong verification", "test_strong_verify"),
    ]),
    Surface("interop", [
        Coverage("interop.mcp_server", "Chimera as an MCP server", "test_mcp_server"),
        Coverage("interop.streaming", "streaming events", "test_streaming"),
    ]),
]


def evidence_from_tests(tests_dir: Path) -> set[str]:
    """The set of test-file stems present — the machine-derived evidence base."""
    return {p.stem for p in Path(tests_dir).glob("test_*.py")}


def score(taxonomy: list[Surface], present: set[str]) -> Scorecard:
    """Score every surface against the present evidence (test stems)."""
    scores: list[SurfaceScore] = []
    for surface in taxonomy:
        proven = [c for c in surface.coverage if c.evidence in present]
        missing = [c.id for c in surface.coverage if c.evidence not in present]
        scores.append(SurfaceScore(surface.name, len(proven), len(surface.coverage), missing))
    return Scorecard(scores)


def score_repo(tests_dir: Path, taxonomy: list[Surface] | None = None) -> Scorecard:
    """Convenience: score the default taxonomy against a repo's tests dir."""
    return score(taxonomy or CHIMERA_TAXONOMY, evidence_from_tests(tests_dir))


def format_scorecard(card: Scorecard) -> str:
    """A compact human-readable rendering for the CLI."""
    lines = [
        f"Chimera maturity: {card.proven}/{card.total} coverage-IDs have their test file — "
        f"{card.level} ({card.ratio:.0%})",
        "  (counts test files that exist, not tests that pass or bench results)",
        "",
    ]
    for s in sorted(card.surfaces, key=lambda x: x.ratio):
        bar = f"{s.proven}/{s.total}"
        gap = f"   missing: {', '.join(s.missing)}" if s.missing else ""
        lines.append(f"  {s.name:<12} {s.level:<5} {bar:>6}  ({s.ratio:.0%}){gap}")
    weak = card.weakest()
    if weak is not None:
        lines += ["", f"weakest surface: {weak.name} ({weak.ratio:.0%}) — the next thing to shore up"]
    return "\n".join(lines)
