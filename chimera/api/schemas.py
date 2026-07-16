"""Pydantic response models for the desktop API.

These exist so the OpenAPI schema (``/api/openapi.json``) describes the exact response shapes, which
lets the frontend GENERATE its TypeScript types from the backend (``npm run gen:api`` →
``src/lib/api-schema.ts``). That is the whole point: the contract lives in one place (here), and the
UI can't drift from it — a shape change regenerates the types and any mismatch becomes a TS error.

The route handlers still build plain dicts; attaching these as ``response_model`` validates and
serializes those dicts against the model (extra keys are dropped), so the models double as a runtime
contract check with no change to the handler bodies.
"""

from __future__ import annotations

from pydantic import BaseModel

# --- health / sessions ----------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: str
    sessions: int


class VersionOut(BaseModel):
    version: str  # the running chimera version (from installed package metadata)
    latest: str | None  # the newest GitHub release tag (no leading "v"); null when the check couldn't run
    update_available: bool  # True ONLY when GitHub confirms a strictly-newer release (honest, fail-silent)
    notes_url: str | None  # the release page to read about the update; null when no update is available


class SessionMetaOut(BaseModel):
    id: str
    title: str
    turns: int
    updated_at: float


class TurnOut(BaseModel):
    user: str
    assistant: str


class SessionDetailOut(BaseModel):
    id: str
    turns: list[TurnOut]


class NewSessionOut(BaseModel):
    id: str


class DeletedOut(BaseModel):
    deleted: bool


# --- config / doctor ------------------------------------------------------------------------------


class TiersOut(BaseModel):
    weak: str
    mid: str
    top: str


class ModelsCfgOut(BaseModel):
    default: str
    weak: str
    mid: str
    orchestrator: str
    cost_mode: str
    cascade: bool
    api_base: str | None
    fallback_models: list[str]
    tiers: TiersOut


class MemoryCfgOut(BaseModel):
    backend: str
    semantic: bool
    auto_consolidate: bool


class CacheCfgOut(BaseModel):
    completion: bool
    prompt: bool


class SandboxCfgOut(BaseModel):
    mode: str
    image: str


class ServerCfgOut(BaseModel):
    token_set: bool


class McpCfgOut(BaseModel):
    autoload: bool  # settings.mcp_autoload — when on, configured MCP tools load at app start


class ProviderOut(BaseModel):
    env: str
    label: str
    set: bool
    hint: str


class ConfigOut(BaseModel):
    models: ModelsCfgOut
    memory: MemoryCfgOut
    cache: CacheCfgOut
    sandbox: SandboxCfgOut
    server: ServerCfgOut
    mcp: McpCfgOut
    providers: list[ProviderOut]


class UpdatedOut(BaseModel):
    updated: list[str]


class DoctorOut(BaseModel):
    has_any_key: bool
    configured_providers: list[str]
    default_model: str
    tiers: TiersOut
    memory_backend: str
    cache: bool
    sandbox: str


class ConfigTestOut(BaseModel):
    ok: bool  # True ONLY after a real 1-token call authenticated — the sole honest "key works" signal
    model: str  # the model the test call used (the given one, or the default)
    error: str | None  # a short, secret-free failure message when ok is False; null on success


# --- memory ---------------------------------------------------------------------------------------


class MemoryItemOut(BaseModel):
    id: str
    content: str
    kind: str
    provenance: str
    source: str


class MemoryProfileOut(BaseModel):
    profile: str
    persona: list[MemoryItemOut]


class MemoryAddOut(BaseModel):
    status: str
    item: MemoryItemOut


# --- skills ---------------------------------------------------------------------------------------


class SkillStatOut(BaseModel):
    name: str
    kind: str
    status: str
    provenance: str
    uses: int
    successes: int
    rate: float | None


class SkillsOut(BaseModel):
    stats: list[SkillStatOut]
    retirement_candidates: list[str]


class ApprovedOut(BaseModel):
    approved: bool


class RetiredOut(BaseModel):
    retired: bool


# --- cron -----------------------------------------------------------------------------------------


class CronJobOut(BaseModel):
    id: str
    name: str
    trigger: str
    schedule: str
    action: str
    enabled: bool
    next_run: float | None
    last_run: float | None
    created_by: str


# --- tasks (kanban + projects) --------------------------------------------------------------------


class TaskCardOut(BaseModel):
    id: str
    title: str
    action: str
    column: str
    success: bool | None
    risk: str | None
    depends_on: list[str]


class ProjectStateOut(BaseModel):
    id: str
    status: str
    iterations: int
    plan_approved: bool
    pending_card_id: str | None
    note: str
    max_iterations: int


class ProjectDetailOut(BaseModel):
    state: ProjectStateOut
    columns: dict[str, list[TaskCardOut]]


# --- usage (cost / usage dashboard) ---------------------------------------------------------------


class UsageTotalsOut(BaseModel):
    turns: int
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    usd: float  # SUM of only the priced turns' cost — unknown prices are excluded, not summed as 0
    unpriced_turns: int  # count of turns whose price was unknown (usd is None)


class UsageDayOut(BaseModel):
    day: str  # "YYYY-MM-DD"
    turns: int
    prompt_tokens: int
    completion_tokens: int
    usd: float  # summed over ONLY the priced turns of this day
    unpriced: int  # turns this day whose price is unknown (usd None) — not folded into usd


class UsageModelOut(BaseModel):
    model: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    usd: float  # summed over ONLY the priced turns of this model
    unpriced: int  # turns of this model whose price is unknown (usd None)


class UsageSessionOut(BaseModel):
    session_id: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    usd: float  # summed over ONLY the priced turns of this session
    unpriced: int  # turns of this session whose price is unknown (usd None)


class UsageSummaryOut(BaseModel):
    totals: UsageTotalsOut
    by_day: list[UsageDayOut]
    by_model: list[UsageModelOut]
    by_session: list[UsageSessionOut]
    cache_hit_pct: float | None  # cache_read / (prompt + cache_read), or None when the denominator is 0
    route_mix: dict[str, int]  # {"single", "fusion", "cascade"} turn counts


# --- plan (planner preview — zero-edit dry run of the planner only) --------------------------------


class PlanOut(BaseModel):
    steps: list[str]  # the planner's concrete numbered steps (empty when the model produced none)
    text: str  # the same steps rendered as numbered lines — the seed for the editable preview
    note: str  # "" on success; a short, secret-free message when the planner call degraded (no 500)


# --- runs (autonomous run receipts) ---------------------------------------------------------------


class FileDiffOut(BaseModel):
    path: str
    patch: str  # a unified-diff body (@@ hunks, +/- lines) — the real change this attempt made
    truncated: bool  # the patch was clipped to the char bound


class AttemptReceiptOut(BaseModel):
    index: int
    verified: bool  # executable evidence passed for this attempt
    reverted: bool  # the workspace was rolled back after this attempt failed
    success: bool
    verify_output: str  # the concrete verifier output (test/assert), truncated
    diff_summary: str  # what this attempt actually changed in the workspace, audited before any revert
    feedback: str  # the retry feedback this attempt produced, truncated
    diffs: list[FileDiffOut]  # real per-file unified diffs (on a reverted attempt: what it ATTEMPTED)


class RunReceiptOut(BaseModel):
    ts: str  # ISO-8601 UTC timestamp of the run's completion
    task: str  # the task text, truncated
    success: bool
    paused: bool  # interrupted for human approval (paused runs aren't persisted; false in practice)
    verify_command: str | None  # the shell command that judged the run, or null (no verifier)
    answer: str  # the final answer, truncated
    attempts: list[AttemptReceiptOut]  # the per-attempt verify-or-revert proof trail


class CancelOut(BaseModel):
    ok: bool  # True when the run_id was known and its stop flag was set; False for a finished/unknown id
    # (a no-op — cancellation is COOPERATIVE: the run halts before its NEXT attempt, never mid model-call)


class BatchCancelOut(BaseModel):
    ok: bool  # True when the batch_id was known AND the request named real task(s); False for a
    # finished/unknown batch or an out-of-range index (a no-op, 200 — never a 404)
    cancelled: int  # how many task stop flags this call actually RAISED (already-stopping tasks don't
    # recount). Cancellation is COOPERATIVE: each task halts before its NEXT attempt, never mid
    # model-call — so this is a count of requests made, not of workers already stopped.


# --- verify (browser screenshot verification artifact) --------------------------------------------


class ScreenshotOut(BaseModel):
    ok: bool  # True ONLY after a real full-page PNG of the given URL was captured and saved
    id: str | None  # the artifact id (uuid hex) to fetch via GET /api/artifacts/{id}; null on failure
    error: str | None  # a short honest message when ok is False (e.g. the missing-browser install hint)


# --- agents (a parallel batch of isolated autonomous runs — the Agent Manager) --------------------
# The terminal ``batch_done`` shape of ``POST /api/agents``. SSE can't carry a ``response_model``, so
# these are surfaced to OpenAPI (and the generated TS types) via ``GET /api/agents/schema`` — exactly
# how ``RunReceiptOut`` reaches the schema through ``GET /api/runs``. Every field is real, already-
# computed evidence: the per-task ``AutonomousResult`` (success/attempts/reverted + its real per-file
# diffs, via ``build_receipt``) and the worktree merge outcome (``conflicts``/``merged``/``is_repo``).


class AgentResultOut(BaseModel):
    index: int  # the task's position in the request (0-based) — tags this task's live event frames
    task: str  # the task text, truncated
    success: bool  # the AutonomousResult's real success flag (verify-or-revert passed)
    attempts: int  # how many verify-or-revert attempts this task took
    reverted: bool  # any attempt was rolled back after verification failed
    changed_paths: list[str]  # files this task's worktree changed (merged back unless a conflict)
    diffs: list[FileDiffOut]  # the terminal attempt's real per-file unified diffs (never fabricated)


class AgentsBatchOut(BaseModel):
    results: list[AgentResultOut]  # one per task, in request order
    conflicts: list[str]  # files ≥2 successful tasks BOTH changed — left UNMERGED, surfaced, never hidden
    merged: int  # changed files copied back to the real workspace across all tasks
    is_repo: bool  # the workspace is a git repo — isolation is REAL only then (else in-place, no isolation)


# --- filesystem (read-only tree + file viewer for the Code screen) --------------------------------


class FsNodeOut(BaseModel):
    name: str  # the entry's base name
    path: str  # its path relative to the workspace (POSIX), the key to expand/open it
    is_dir: bool


class FsTreeOut(BaseModel):
    workspace: str  # the resolved workspace root this listing is scoped to
    path: str  # the (relative) directory listed
    entries: list[FsNodeOut]  # immediate children only (dirs first, then files, alphabetical)
    capped: bool  # the listing hit the max-entries cap (some children are omitted)


class FsFileOut(BaseModel):
    path: str  # the (relative) file read
    content: str  # UTF-8 text, truncated at the read cap; empty for a binary/dir/missing file
    truncated: bool  # the content was clipped at the read cap
    note: str  # a short honest note ("binary or non-text", "not found") or "" for a clean read


class FsFileWrittenOut(BaseModel):
    path: str  # the (relative) file written
    bytes: int  # bytes actually written to disk (may exceed content length on a CRLF-preserved file)


# --- git (status / diff / commit / scoped revert for the Code screen's git panel) ------------------


class GitFileOut(BaseModel):
    path: str  # the changed file's path relative to the repo root (rename → the new name)
    x: str  # the index (staged) status char from porcelain XY (" " when unstaged)
    y: str  # the worktree status char from porcelain XY (" " when the change is only staged)
    staged: bool  # the change is present in the index (x is a real status and it isn't untracked)
    untracked: bool  # git doesn't track this file yet (porcelain "??")


class GitStatusOut(BaseModel):
    is_repo: bool  # False when the folder isn't a git repo (or git is missing) — the honest empty-state
    branch: str  # the current branch ("" when not a repo, or detached/no-commits-yet edge cases)
    files: list[GitFileOut]  # changed files (empty when the tree is clean)


class GitDiffOut(BaseModel):
    is_repo: bool  # False when the folder isn't a git repo (or git is missing)
    patch: str  # the real unified-diff body (@@ hunks, +/- lines); "" when there's no diff


class GitCommitOut(BaseModel):
    ok: bool  # True only after a real, non-zero-free `git commit` — explicit paths staged, never add -A
    commit: str  # the short HEAD hash on success; "" otherwise
    output: str  # the combined git stdout+stderr, truncated
    error: str | None  # a short git error when ok is False; null on success


class GitRevertOut(BaseModel):
    ok: bool  # True when the scoped revert completed (git checkout + clean on the passed paths)
    reverted: list[str]  # the paths the revert was scoped to (echoed back on success)
    error: str | None  # a short git error when ok is False; null on success


# --- governance / security (injection red-team scoreboard + audit log) -----------------------------


class InjectionCategoryOut(BaseModel):
    category: str  # destructive | backdoor | exfil | self_modify
    defended_asr: float  # attack-success-rate for this category WITH defenses (lower = better)
    undefended_asr: float  # same category WITHOUT defenses — the baseline the defenses improve on
    count: int  # number of attacks in this category


class InjectionAttackOut(BaseModel):
    id: str
    category: str
    harmful_tool: str  # the tool the attacker wanted invoked
    blocked_defended: bool  # the defenses stopped it
    blocked_undefended: bool  # it was stopped even bare (baseline) — false for every real attack


class InjectionReportOut(BaseModel):
    total_attacks: int
    defended_asr: float  # overall attack-success-rate WITH defenses (fraction still getting through)
    undefended_asr: float  # overall ASR WITHOUT defenses (the honest baseline)
    defended_block_rate: float
    undefended_block_rate: float
    by_category: list[InjectionCategoryOut]
    attacks: list[InjectionAttackOut]
    leaks_defended: list[str]  # attack ids that get through EVEN defended — the named honest gap


class AuditEventOut(BaseModel):
    seq: int
    type: str
    summary: str  # a short human string flattened from the entry's remaining (arbitrary) keys


class GovernanceAuditOut(BaseModel):
    events: list[AuditEventOut]  # newest-first (highest seq first)
    count: int
    populated: bool  # False when the audit file has no entries — drives the honest empty-state


# --- memory layers (by-kind + provenance + by-source view) ----------------------------------------


class MemoryLayerOut(BaseModel):
    kind: str  # working | episodic | semantic | persona (+ any unknown kind, folded in trailing)
    count: int
    clean: int  # items in this kind with provenance "clean"
    tainted: int  # items in this kind with provenance "tainted" (shown as "unverified")


class MemorySourceOut(BaseModel):
    source: str  # origin app (e.g. "chimera", "hermes"); "" when blank (UI shows "—")
    count: int


class MemoryLayersOut(BaseModel):
    total: int
    clean: int  # overall items with provenance "clean"
    tainted: int  # overall items with provenance "tainted"
    layers: list[MemoryLayerOut]  # ALWAYS the 4 canonical kinds (0-count included) + any unknown
    by_source: list[MemorySourceOut]  # top 20, count desc
    # Pass-through of settings.semantic_memory (opt-in, off by default). A boolean flag for an honest UI
    # note only — NOT an embeddings index count; no such index exists when it is False.
    semantic_embeddings_enabled: bool


# --- tools (agent tool registry inventory) --------------------------------------------------------


class ToolInfoOut(BaseModel):
    name: str
    description: str
    params: list[str]  # the tool's parameter NAMES (parameters.properties keys); [] when it takes none
    tags: list[str]  # capability tags derived purely from the tool NAME vs the governance sets, in a
    # stable order: network / read / write / exec / side-effect. [] when the name is in none of them.
    untrusted_output: bool  # True only for MCP/OpenAPI-imported tools; False for native tools (read as-is)


class ToolsOut(BaseModel):
    tools: list[ToolInfoOut]
    count: int


# --- MCP / Integrations (configured servers + live test) ------------------------------------------


class McpServerOut(BaseModel):
    name: str
    command: str
    args: list[str]
    env_keys: list[str]  # env variable NAMES only — the secret VALUES are never returned


class McpServersOut(BaseModel):
    servers: list[McpServerOut]
    count: int


class McpToolOut(BaseModel):
    name: str
    description: str


class McpTestOut(BaseModel):
    ok: bool  # True ONLY after a real stdio connect + tool enumeration — the sole "connected" signal
    tools: list[McpToolOut]  # the tools the server exposed on a successful connect; [] otherwise
    error: str | None  # a short, secret-free failure message when ok is False; null on success


class McpAddRequest(BaseModel):
    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}  # accepted on write (stored locally), never echoed back in reads


# --- maturity (self-eval coverage scorecard by surface) -------------------------------------------


class MaturitySurfaceOut(BaseModel):
    name: str  # the surface (fusion, evolution, governance, memory, benchmarks, resilience, interop)
    proven: int  # coverage-IDs whose evidence test-file exists (presence, NOT that it passes)
    total: int  # coverage-IDs that constitute the surface
    ratio: float  # proven/total, 0..1
    level: str  # GA (>=0.9) / Beta (>=0.5) / Alpha — the maturity band
    missing: list[str]  # coverage-IDs with no evidence test-file yet (the honest gap)


class MaturityWeakestOut(BaseModel):
    name: str  # the surface with the lowest coverage — the evolution loop's next objective
    ratio: float


class MaturityOut(BaseModel):
    available: bool  # False when neither a live test suite nor the shipped snapshot could be read
    source: str | None  # "live" (globbed the real tests dir) | "snapshot" (shipped fallback) | null
    proven: int  # overall coverage-IDs proven across all surfaces
    total: int
    ratio: float
    level: str
    surfaces: list[MaturitySurfaceOut]
    weakest: MaturityWeakestOut | None  # null when every surface is fully proven
    generated_for: str | None  # the chimera version a snapshot was generated for (null when unavailable)


# --- benchmarks (the app's REAL, recorded performance numbers — honestly framed) -------------------


class BenchmarkDiscordantOut(BaseModel):
    treatment_only: int  # tasks Chimera passed that the bare model failed (the lift's real evidence)
    baseline_only: int  # tasks the bare model passed that Chimera failed


class BenchmarkLiftOut(BaseModel):
    suite: str  # the suite label — makes clear this is the internal suite, NOT SWE-bench/Terminal-Bench
    model: str  # the cheap/weak model both arms ran (Chimera's lift is over the SAME model, bare)
    n: int  # paired-task count — SMALL; the caveat travels with the number
    baseline_rate: float  # the bare model's pass rate (0..1)
    treatment_rate: float  # the model + Chimera's pass rate (0..1)
    delta: float  # treatment - baseline (0..1); the +50pp lift
    ci: list[float]  # 95% paired CI [lo, hi] — INCLUDES 0 here, hence not significant
    significant: bool  # False: promising but not statistically significant at this n
    source: str  # the committed results file this block is read from
    note: str  # the honest one-liner (promising, n=6, not significant, no re-rolling)


class BenchmarkExternalOut(BaseModel):
    benchmark: str  # the external benchmark's name (e.g. Terminal-Bench)
    model: str  # the model both arms ran
    n: int  # task count
    baseline_rate: float  # bare model pass rate (0..1)
    treatment_rate: float  # + Chimera scaffold pass rate (0..1)
    delta: float  # treatment - baseline (0..1); negative here — the humbling number
    ci: list[float]  # 95% paired CI [lo, hi] — includes 0
    significant: bool  # False
    source: str  # the committed RESULTS.md this block is cited from
    note: str  # why it didn't lift (already-competent model, not the weak regime) — published anyway


class BenchmarksOut(BaseModel):
    available: bool  # False when the shipped snapshot couldn't be read — honest empty-state, never a 500
    internal_lift: BenchmarkLiftOut | None  # the promising weak-model lift (null when unavailable)
    external: list[BenchmarkExternalOut]  # recorded external results (e.g. Terminal-Bench); [] otherwise
    generated_for: str | None  # the chimera version the snapshot was generated for (null when unavailable)
