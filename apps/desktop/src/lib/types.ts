// The API response types are GENERATED from the backend's OpenAPI schema (see `api-schema.ts`, built
// by `npm run gen:api`). Re-exporting them here means the UI can't drift from the backend: if a
// response model changes, regenerating the schema changes these types and any mismatch is a TS error.
//
// The chat stream is Server-Sent Events, not a typed HTTP body, so its event payloads (TurnReport,
// ToolEvent) and the pure UI types (Message/Role) are hand-written below — they have no OpenAPI schema.

import type { components } from "@/lib/api-schema";

type Schemas = components["schemas"];

export type SessionMeta = Schemas["SessionMetaOut"];
export type ChatTurn = Schemas["TurnOut"];
export type MemoryItem = Schemas["MemoryItemOut"];
export type MemoryLayers = Schemas["MemoryLayersOut"];
export type SkillStat = Schemas["SkillStatOut"];
export type CronJob = Schemas["CronJobOut"];
export type TaskCard = Schemas["TaskCardOut"];
export type ProjectState = Schemas["ProjectStateOut"];
export type ProviderCfg = Schemas["ProviderOut"];
export type AppConfig = Schemas["ConfigOut"];
export type DoctorInfo = Schemas["DoctorOut"];
export type ConfigTest = Schemas["ConfigTestOut"];
export type UsageSummary = Schemas["UsageSummaryOut"];
export type RunReceipt = Schemas["RunReceiptOut"];
export type AttemptReceipt = Schemas["AttemptReceiptOut"];
export type InjectionReport = Schemas["InjectionReportOut"];
export type GovernanceAudit = Schemas["GovernanceAuditOut"];
export type ToolInfo = Schemas["ToolInfoOut"];
export type Tools = Schemas["ToolsOut"];
export type Maturity = Schemas["MaturityOut"];
export type MaturitySurface = Schemas["MaturitySurfaceOut"];

// --- SSE event payloads + UI-only types (not in the OpenAPI schema) ---

// The per-turn fusion/cascade trace. Hand-typed to mirror the neutral dict the backend attaches to the
// SSE `done` payload (see chimera/fusion/engine.py + cascade.py) — NOT part of the generated schema.

export interface FusionPanelEntry {
  model: string;
  content: string;
  error: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
}

export interface FusionStage {
  stage: string;
  model: string;
  prompt_tokens: number | null;
  completion_tokens: number | null;
}

export interface FusionMeta {
  kind: "fusion";
  aggregation: string;
  early_stopped: boolean;
  diversity: number | null;
  panel: FusionPanelEntry[];
  judge_analysis: string;
  stages: FusionStage[];
}

export interface CascadeMeta {
  kind: "cascade";
  tiers_tried: string[];
  accepted_tier: string;
  models: Record<string, string>;
  tokens_by_tier: Record<string, number>;
  agreement: number | null;
  fuse_reason: string;
  fusion?: FusionMeta;
}

export type RouteMeta = FusionMeta | CascadeMeta;

export interface TurnReport {
  session_id: string;
  answer: string;
  prompt_tokens: number;
  completion_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  usd: number | null;
  tool_names: string[];
  memory_facts_used: number;
  memory_layer: string | null;
  steps: number;
  stopped_reason: string;
  route_meta?: RouteMeta | null;
}

export interface ToolEvent {
  name: string;
  ok: boolean;
}

export type Role = "user" | "assistant";

export interface Message {
  role: Role;
  content: string;
}
