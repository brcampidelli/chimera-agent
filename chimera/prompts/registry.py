"""Every prompt the product sends a model, known in one place.

Study 25 (`bench/PLAN-study25-system-prompts.md` §5.3, wave 0) found about ninety prompt texts spread
across forty modules, with nothing that listed them. That made several defects invisible:
- five different fence syntaxes;
- two opposite language rules;
- worker prompts that silently replaced the base prompt and lost its untrusted-data sentence.

None of these was wrong in any one file. They were wrong together, and no file showed the whole.

This registry is the whole. Each entry names:
- where a prompt lives;
- which layer of the stack it belongs to;
- the situations (S1–S15 of the plan) that send it;
- what is known about it: `measured`, `null` (measured, and it did nothing) or `unmeasured`, and
  the bench or run that says so.

**The strings stay where they are.** The plan said the prompts would move into this package. They
did not, deliberately. Each of them carries, in a comment beside it, the measurement that justified
its wording, and that comment belongs next to the code that sends the text. The registry points at
the strings instead of owning them. What guards the bytes is the snapshot under
`tests/prompt_snapshots/`: an edit to any registered prompt shows up as a reviewed diff.

**Two kinds of entry.**
- A *constant* is a module-level string, or a value derived from one by a small renderer. It is
  snapshotted.
- An *inline* entry is text composed inside a function, from runtime values. It is listed with a
  pointer, not snapshotted. The pointer is checked to still resolve, so a renamed function cannot
  leave a dead entry behind.

`tests/test_every_prompt_is_registered.py` scans the package for prompt-shaped constants. A new one
that is not listed here fails the suite, so a prompt cannot be added without being registered.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Layer = Literal[
    "core",  # the base every agent-loop situation starts from
    "situation",  # a role or module that frames one situation
    "surface",  # an output contract for one surface (voice, CLI, desktop, Discord)
    "tool",  # text that exists because a tool exists (a tool description, the todo sentence)
    "turn",  # a user turn the harness injects mid-run (a nudge)
    "call",  # the system prompt of a separate model call with no tools (judge, grader, planner)
    "volatile",  # per-turn context: facts, jobs, cards, restored-turn labels
    "project",  # the repository's own instructions
    "owner",  # the owner's identity (agent.json)
    "marker",  # a delimiter the model is told to recognise (the fence)
]
Status = Literal["measured", "null", "unmeasured"]

#: The situations of the plan. `eval` is text that only a bench sends.
SITUATIONS: tuple[str, ...] = tuple(f"S{i}" for i in range(1, 16)) + ("eval",)


@dataclass(frozen=True)
class PromptSection:
    """One prompt the product sends, with what is known about it."""

    id: str
    #: ``module:attribute`` (attributes may chain with dots). For an inline entry, the function or
    #: class that composes the text.
    source: str
    layer: Layer
    situations: tuple[str, ...]
    status: Status
    #: The bench, test or run behind ``status``. Empty only when nothing was ever run.
    evidence: str = ""
    #: False for text composed at runtime: listed and pointed at, but there are no fixed bytes to keep.
    snapshot: bool = True
    #: Builds the text when the source is not itself a string (a tuple of approaches, a role object).
    render: Callable[[], str] | None = None
    note: str = ""

    def resolve(self) -> object:
        """The object ``source`` names. Raises if the pointer has gone stale."""
        module_name, _, attr = self.source.partition(":")
        obj: object = importlib.import_module(module_name)
        for part in attr.split(".") if attr else ():
            obj = getattr(obj, part)
        return obj

    def text(self) -> str | None:
        """The exact bytes sent, for a snapshotted entry; None for an inline one."""
        if not self.snapshot:
            return None
        if self.render is not None:
            return self.render()
        obj = self.resolve()
        if not isinstance(obj, str):
            raise TypeError(f"{self.source} is {type(obj).__name__}, not a string; give it a render")
        return obj


def _crew_approaches() -> str:
    from chimera.orchestration.approaches import APPROACHES

    return "\n\n".join(f"[{a.id}]\n{a.instruction}" for a in APPROACHES)


def _restored_labels() -> str:
    from chimera.interface.session import _RESTORED

    return "\n".join(f"{key}:{label}" for key, label in sorted(_RESTORED.items()))


def _fence_example() -> str:
    from chimera.governance.ledger_tool import fence

    return fence("<the untrusted content goes here>")


_ALL = ("S1", "S2", "S3", "S5", "S10", "S11")
_LOOP = ("S1", "S2", "S3", "S5")


def _c(
    id: str,
    source: str,
    layer: Layer,
    situations: tuple[str, ...],
    status: Status,
    evidence: str = "",
    *,
    render: Callable[[], str] | None = None,
    note: str = "",
) -> PromptSection:
    return PromptSection(id, source, layer, situations, status, evidence, True, render, note)


def _i(
    id: str,
    source: str,
    layer: Layer,
    situations: tuple[str, ...],
    status: Status = "unmeasured",
    evidence: str = "",
    note: str = "",
) -> PromptSection:
    return PromptSection(id, source, layer, situations, status, evidence, False, None, note)


SECTIONS: tuple[PromptSection, ...] = (
    # ---- the agent loop ------------------------------------------------------------------------
    _c("loop.default", "chimera.core.agent:DEFAULT_SYSTEM_PROMPT", "core", _ALL, "measured",
       "ask exception: tests/test_the_agent_may_ask.py (paired run); fence: bench/right_hand_governance"),
    _c("loop.todo", "chimera.core.agent:TODO_PROMPT", "tool", _LOOP, "unmeasured",
       note="four runs per model, see the comment in Agent.run; too few to call measured"),
    _c("loop.action_nudge", "chimera.core.agent:_ACTION_NUDGE", "turn", ("S1",), "measured",
       "bench/swe_bench/RESULTS.md measured the trigger (empty patches), not this wording"),
    _i("loop.stop_nudge", "chimera.core.agent:Agent.run", "turn", _LOOP, "measured",
       "bench/tool_loop_fix measured the breaker; the sentence itself is unmeasured"),
    _i("loop.final_nudge", "chimera.core.agent:Agent.run", "turn", _LOOP),
    _i("loop.unanswered_call_stub", "chimera.core.agent:Agent.run", "turn", _LOOP),
    _i("loop.prefix_nonce", "chimera.core.agent:Agent.run", "core", ("eval",),
       note="bench instrument only; empty unless CHIMERA_PREFIX_NONCE is set"),
    _i("loop.skills_block", "chimera.skills.retrieval:skills_context_block", "volatile", _ALL),
    _i("loop.bundles_block", "chimera.core.agent:Agent._bundle_context", "volatile", _ALL),
    _c("loop.cards_instruction", "chimera.evolution.card_retrieval:_INSTRUCTION", "volatile",
       ("S1", "S2"), "null", "bench/skillcard (66.7→83.3%, n=12; kept off)"),
    _i("loop.cards_block", "chimera.evolution.card_retrieval:cards_context_block", "volatile",
       ("S1", "S2")),
    _i("loop.project_instructions", "chimera.core.agents_md:load_agent_instructions", "project", _ALL),
    _i("loop.owner_identity", "chimera.core.instructions:render", "owner", ("S2", "S3", "S5")),
    # ---- compaction and memory -----------------------------------------------------------------
    _i("compaction.structural_note", "chimera.core.context_budget:compact", "volatile", ("S13",),
       "measured", "bench/compaction: fired 0 times in 137 real runs"),
    _i("compaction.restore", "chimera.core.context_budget:RunState.as_message", "volatile", ("S13",)),
    _c("compaction.summariser", "chimera.core.summarise:SYSTEM", "call", ("S13", "S2"), "measured",
       "bench/compaction: 25/30 vs 6/30 (+63 pp, p=3.8e-6)"),
    _i("memory.consolidate", "chimera.memory.consolidate:model_summarizer", "call", ("S13",)),
    _i("memory.persona_preamble", "chimera.memory.manager:MemoryManager.profile", "volatile",
       ("S3", "S10")),
    # ---- the tool router (off by default; #537 measured it worse) ------------------------------
    _i("router.prompt", "chimera.core.tool_router:ToolRouter._prompt", "call", ("S1",), "measured",
       "bench/tool_router (#537): the router made every executor worse"),
    _i("router.hint_message", "chimera.core.tool_router:hint_message", "turn", ("S1",), "measured",
       "bench/tool_router_hint (#537)"),
    # ---- the Code screen and voice -------------------------------------------------------------
    _i("code.recalled_facts", "chimera.api.code_api:register_code_api", "volatile", ("S2", "S4"),
       note="composed in build_agent"),
    _i("code.image_note", "chimera.api.code_api:register_code_api", "volatile", ("S2",),
       note="composed in _launch_turn"),
    _i("code.jobs_note", "chimera.api.code_api:register_code_api", "volatile", ("S2", "S4"),
       note="composed in _launch_turn"),
    _i("code.attached_document", "chimera.api.code_api:register_code_api", "volatile", ("S2",),
       note="composed in _launch_turn"),
    _c("voice.spoken", "chimera.api.code_api:SPOKEN_NOTE", "surface", ("S4",), "unmeasured",
       note="live tests of 2026-09-17/18 are cited in the comment; no bench"),
    _i("voice.works_note", "chimera.api.works:WorkManager.note", "volatile", ("S4",)),
    _i("voice.work_tools", "chimera.api.works:WorkStatusTool", "tool", ("S4",)),
    _c("plan.gate", "chimera.api.plan_gate:_PLAN_GATE_SYSTEM", "call", ("S2", "S14"), "unmeasured"),
    _i("plan.approved_note", "chimera.api.plan_gate:as_system_note", "volatile", ("S2", "S14")),
    _c("plan.planner", "chimera.core.planner:_PLANNER_SYSTEM", "call", ("S1", "S14"), "null",
       "bench/harness_bench arm C (+0.003, inside SD 0.073)"),
    # ---- terminal chat, Discord, webhooks ------------------------------------------------------
    _i("chat.layout", "chimera.interface.session:ChatSession._assemble", "volatile", ("S3", "S10")),
    _c("chat.restored_labels", "chimera.interface.session:_RESTORED", "volatile", ("S3", "S10"),
       "unmeasured", render=_restored_labels),
    _i("chat.profile", "chimera.interface.profile:render_profile", "volatile", ("S3", "S10")),
    _i("webhook.payload", "chimera.cli.main:_webhook_handler", "volatile", ("S10",),
       note="payload appended to the job prompt; docs/audits/sleeper-channels.md channel 4"),
    # ---- autonomous solve ----------------------------------------------------------------------
    _i("solve.compose", "chimera.core.autonomous:AutonomousAgent._compose", "volatile", ("S1", "S5")),
    _i("solve.context_blocks", "chimera.core.autonomous:AutonomousAgent.run", "volatile", ("S1",),
       "null", "bench/harness_bench: repo-map −0.012, checklist +0.005, planner +0.003 (SD 0.073)"),
    _i("solve.feedback_fragments", "chimera.core.autonomous:AutonomousAgent.run", "turn", ("S1",)),
    _c("solve.diff_feedback_header", "chimera.core.autonomous:_DIFF_FEEDBACK_HEADER", "turn", ("S1",),
       "null", "bench/retry_lift (closed without proof: +6% and −4%)"),
    _i("solve.recovery_briefs", "chimera.core.failure_class:targeted_feedback", "turn", ("S1",),
       "null", "bench/retry_lift"),
    _c("solve.manager", "chimera.core.supervisor:_MANAGER_SYSTEM", "call", ("S1", "S15"), "measured",
       "bench/manager_p (prose only: approves 5/246); bench/manager_diff (TPR 0.19, FPR 0.01)"),
    _i("solve.rubric_judge", "chimera.eval.rubric:model_judge", "call", ("S1", "S8")),
    _c("solve.strong_verify", "chimera.core.strong_verify:_VERIFY_SYSTEM", "call", ("S1", "S8"),
       "null", "bench/verifier_by_uncertainty: fired 0 of 385"),
    _c("solve.checklist_extract", "chimera.core.checklist:_EXTRACT_SYSTEM", "call", ("S1", "S8"),
       "null", "bench/harness_bench"),
    _c("solve.checklist_grade", "chimera.core.checklist:_GRADE_SYSTEM", "call", ("S1", "S8"), "null",
       "bench/harness_bench"),
    _c("solve.spec_test", "chimera.core.spec_test:_GEN_SYSTEM", "call", ("S1", "S8"), "measured",
       "bench/spec_test_vacuity (48% pass on buggy code); bench/test_gate_two_sided"),
    _c("solve.progress_ledger", "chimera.core.ledger:_LEDGER_SYSTEM", "call", ("S1",), "unmeasured"),
    _i("solve.task_ledger", "chimera.core.ledger:TaskLedger.context", "turn", ("S1",)),
    # ---- hierarchy, crew, sub-agents -----------------------------------------------------------
    _c("hierarchy.worker", "chimera.orchestration.hierarchy:WORKER_SYSTEM", "situation", ("S6",),
       "measured", "bench/hierarchy_equal_calls; bench/hierarchy_multistep"),
    _c("hierarchy.decompose", "chimera.orchestration.hierarchy:_DECOMPOSE_SYSTEM", "call", ("S6",),
       "unmeasured"),
    _c("hierarchy.synthesise", "chimera.orchestration.hierarchy:_SYNTH_SYSTEM", "call", ("S6",),
       "unmeasured"),
    _c("hierarchy.synthesise_verbatim", "chimera.orchestration.hierarchy:_SYNTH_VERBATIM", "call",
       ("S6",), "measured", "bench/hierarchy_equal_calls (+26 pp at 10 tasks → +3 pp at 30)"),
    _i("hierarchy.task_spec", "chimera.orchestration.spec:TaskSpec.render", "volatile", ("S6",)),
    _i("hierarchy.synthesis_user",
       "chimera.orchestration.hierarchy:HierarchicalOrchestrator._synthesize", "turn", ("S6",)),
    _c("crew.approaches", "chimera.orchestration.approaches:APPROACHES", "situation", ("S6",),
       "unmeasured", render=_crew_approaches,
       note="each approach becomes a crew worker's WHOLE system prompt"),
    _i("crew.supervisor", "chimera.api.orchestration_api:register_orchestration_api", "situation",
       ("S6",), note="composed in crew_stream.work"),
    _i("crew.synthesis", "chimera.orchestration.crew:SupervisorCrew.run", "turn", ("S6",)),
    _i("crew.demo_roles", "chimera.orchestration.crew:demo_crew", "situation", ("S6",)),
    _c("lifecycle.reviewer", "chimera.orchestration.lifecycle:_REVIEWER.system_prompt", "situation",
       ("S6", "S15"), "unmeasured"),
    _c("subagent.system", "chimera.core.subagent:SUBAGENT_SYSTEM", "situation", ("S6", "S12"),
       "unmeasured"),
    _i("subagent.tool_description", "chimera.core.subagent:SubAgentTool", "tool", ("S6", "S12")),
    _c("explorer.system", "chimera.core.explorer:EXPLORER_SYSTEM", "situation", ("S12",),
       "unmeasured"),
    _i("explorer.task", "chimera.core.explorer:ContextExplorer.explore", "turn", ("S12",)),
    _i("brief.recipe", "chimera.orchestration.brief:brief_task", "turn", ("S5", "S10")),
    _c("spec.draft", "chimera.orchestration.draft:_SYSTEM", "call", ("S14",), "unmeasured"),
    # ---- fusion --------------------------------------------------------------------------------
    _c("fusion.judge", "chimera.fusion.engine:_JUDGE_SYSTEM", "call", ("S7",), "measured",
       "bench/judge_blind (blind is the default); bench/judge_blind_prose (0/180)"),
    _c("fusion.synthesise", "chimera.fusion.engine:_SYNTH_SYSTEM", "call", ("S7",), "unmeasured"),
    _c("fusion.synthesise_agreed", "chimera.fusion.engine:_SYNTH_AGREED_SYSTEM", "call", ("S7",),
       "unmeasured"),
    _i("fusion.user_templates", "chimera.fusion.engine:FusionEngine._present", "turn", ("S7",)),
    _c("fusion.consistency", "chimera.fusion.consistency:_SYNTH_SYSTEM", "call", ("S7",),
       "unmeasured"),
    _c("fusion.verifier_score", "chimera.fusion.verifier_select:_SCORE_SYSTEM", "call",
       ("S7", "S8"), "unmeasured"),
    # ---- verifiers of an orchestration envelope ------------------------------------------------
    _c("envelope.dropped_only", "chimera.orchestration.envelope_verify:_SPOT_SYSTEM_DROPPED_ONLY",
       "call", ("S6", "S8"), "measured", "bench/blind_audit: caught 23/23, 20/23 as a clause"),
    _c("envelope.three_checks", "chimera.orchestration.envelope_verify:_SPOT_SYSTEM_THREE_CHECKS",
       "call", ("S8",), "measured", "bench/blind_audit: passed 19 of 23 drops (legacy arm)"),
    _c("envelope.blind_extract", "chimera.orchestration.envelope_verify:EXTRACT_SYSTEM", "call",
       ("S8",), "measured", "bench/blind_audit: blind form caught 19/23, cries wolf on half"),
    _c("envelope.blind_compare", "chimera.orchestration.envelope_verify:COMPARE_SYSTEM", "call",
       ("S8",), "measured", "bench/blind_audit"),
    # ---- governance and typed decisions --------------------------------------------------------
    _c("governance.judge", "chimera.decisions.governance:JUDGE_TEXT", "call", ("S9",), "measured",
       "bench/governance_judge (9/9, 0/10); bench/perturbation_floor (framing: 8–9 of 14 to ALLOW)"),
    _c("governance.hosted_advisory", "chimera.decisions.hosted:ADVISORY", "call", ("S9",), "measured",
       "bench/jev_decisions"),
    _i("governance.hosted_system", "chimera.decisions.hosted:HostedVerbalizedBackend.system_text",
       "call", ("S9",)),
    _i("governance.local_system", "chimera.decisions.local:LocalLogprobBackend.system_text", "call",
       ("S9",), "measured", "bench/jevbench_local (0.619)"),
    _c("governance.quarantine", "chimera.governance.quarantine:_QUARANTINE_SYSTEM", "call",
       ("S9", "S11", "S12"), "unmeasured"),
    _i("governance.quarantine_user", "chimera.governance.quarantine:QuarantinedReader._prompt",
       "turn", ("S9", "S11", "S12")),
    _c("fence.open", "chimera.governance.ledger_tool:FENCE_OPEN", "marker", _ALL, "measured",
       "bench/right_hand_governance (0/12 → 12/12)"),
    _c("fence.close", "chimera.governance.ledger_tool:FENCE_CLOSE", "marker", _ALL, "measured",
       "bench/right_hand_governance"),
    _c("fence.wrapped", "chimera.governance.ledger_tool:fence", "marker", _ALL, "measured",
       "bench/right_hand_governance", render=_fence_example),
    _i("tool.decide_description", "chimera.tools.decide:DecideTool", "tool", ("S9",)),
    _c("tool.browser_viewport_first", "chimera.tools.browser:_VIEWPORT_FIRST_DESCRIPTION", "tool",
       ("S11",), "measured", "bench/browser_viewport_tasks (lost to the shipped description)"),
    # ---- self-evolution ------------------------------------------------------------------------
    _c("evolution.propose", "chimera.evolution.evolver:_PROPOSE_SYSTEM", "call", ("S13",), "null",
       "bench/learning_lift"),
    _c("evolution.propose_antipattern", "chimera.evolution.evolver:_PROPOSE_ANTIPATTERN_SYSTEM",
       "call", ("S13",), "null", "bench/learning_lift"),
    _c("evolution.refine", "chimera.evolution.evolver:_REFINE_SYSTEM", "call", ("S13",), "null",
       "bench/learning_lift"),
    _c("evolution.distill", "chimera.evolution.evolver:_DISTILL_SYSTEM", "call", ("S13",), "null",
       "bench/learning_lift"),
    _c("evolution.gepa_execute", "chimera.evolution.gepa:_EXECUTE_SYSTEM", "call", ("S13",),
       "unmeasured"),
    _c("evolution.gepa_reflect", "chimera.evolution.gepa:_REFLECT_SYSTEM", "call", ("S13",),
       "unmeasured"),
    _c("evolution.learned_skill", "chimera.evolution.learned_skill:_LEARNED_SYSTEM", "call",
       ("S13",), "unmeasured"),
    _c("evolution.playbook_curate", "chimera.evolution.playbook:_CURATE_SYSTEM", "call", ("S13",),
       "unmeasured"),
    _c("ecosystem.meta_agent", "chimera.ecosystem.meta_agent:_DESIGN_SYSTEM", "call", ("S6",),
       "unmeasured"),
    # ---- deliverables and built-in skills ------------------------------------------------------
    _i("deliver.system", "chimera.deliver:deliverable_system_prompt", "call", ("S1", "S5")),
    _i("skills.builtin_llm", "chimera.skills.builtin.code_skills:CompleteCodeSkill", "call", (),
       note="never called by the agent loop; only evolver and holdout run skills"),
    # ---- bench-only ----------------------------------------------------------------------------
    _c("eval.swe_bench", "chimera.eval.swe_bench:_INSTRUCTION", "call", ("eval",), "unmeasured"),
    _c("eval.authorization", "chimera.eval.authorization:INSTRUCTION", "call", ("eval",),
       "unmeasured"),
    _c("eval.hierarchy_multistep", "chimera.eval.hierarchy_multistep:_SYS_BASE", "call", ("eval",),
       "unmeasured"),
)

#: Constants whose names look like prompts but are never sent to a model. Each says why, so the scan
#: in the test cannot be satisfied by quietly adding a name here.
NOT_PROMPTS: dict[str, str] = {
    "chimera.api.plan_gate:REASON": "shown to the person on the approval card; no model reads it",
    "chimera.migration.base:_MEMORY_NOTE": "a CLI message printed by `chimera migrate`",
}


def by_id(section_id: str) -> PromptSection:
    """The registered section with this id."""
    for section in SECTIONS:
        if section.id == section_id:
            return section
    raise KeyError(section_id)
