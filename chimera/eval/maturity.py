"""Maturity scorecard (M15-B5) — a self-eval spine over Chimera's surfaces.

OpenClaw scores its own maturity with a taxonomy of surfaces × coverage-IDs tied to QA evidence, so
"is this done?" becomes an auditable rubric instead of a vibe. This is the Chimera version: each
surface (fusion, evolution, governance, memory, benchmarks, resilience, interop) declares the
capabilities that define it, and each capability's **evidence is a real test** — proven iff that
test exists. The result is machine-derived (glob the tests dir), not a self-assessment, and doubles
as a per-surface objective function for the evolution loop: the weakest surface / the missing
coverage-IDs are exactly what to shore up next.

Honesty note: a passing *test presence* is a proxy for "covered", not proof of correctness — a
renamed or deleted test correctly shows up as a coverage gap. That is the point; the scorecard flags
drift rather than hiding it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Coverage:
    """One capability that defines a surface, proven by a named test file's presence."""

    id: str
    description: str
    evidence: str  # a test-file stem (e.g. "test_diff_gate") whose presence proves this capability


@dataclass
class Surface:
    """A product surface and the coverage-IDs that constitute it (AND-ed)."""

    name: str
    coverage: list[Coverage] = field(default_factory=list)


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
        """Alpha (<50%) / Beta (50–90%) / GA (≥90%) — the OpenClaw-style maturity band."""
        r = self.ratio
        return "GA" if r >= 0.9 else "Beta" if r >= 0.5 else "Alpha"


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
        r = self.ratio
        return "GA" if r >= 0.9 else "Beta" if r >= 0.5 else "Alpha"

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
    lines = [f"Chimera maturity: {card.proven}/{card.total} coverage-IDs proven — {card.level} ({card.ratio:.0%})", ""]
    for s in sorted(card.surfaces, key=lambda x: x.ratio):
        bar = f"{s.proven}/{s.total}"
        gap = f"   missing: {', '.join(s.missing)}" if s.missing else ""
        lines.append(f"  {s.name:<12} {s.level:<5} {bar:>6}  ({s.ratio:.0%}){gap}")
    weak = card.weakest()
    if weak is not None:
        lines += ["", f"weakest surface: {weak.name} ({weak.ratio:.0%}) — the next thing to shore up"]
    return "\n".join(lines)
