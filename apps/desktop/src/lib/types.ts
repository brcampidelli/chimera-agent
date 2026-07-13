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
export type SkillStat = Schemas["SkillStatOut"];
export type CronJob = Schemas["CronJobOut"];
export type TaskCard = Schemas["TaskCardOut"];
export type ProjectState = Schemas["ProjectStateOut"];
export type ProviderCfg = Schemas["ProviderOut"];
export type AppConfig = Schemas["ConfigOut"];
export type DoctorInfo = Schemas["DoctorOut"];

// --- SSE event payloads + UI-only types (not in the OpenAPI schema) ---

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
