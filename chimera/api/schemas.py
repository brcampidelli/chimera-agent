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
