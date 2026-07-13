// Hand-written now; the plan's fast-follow generates these from the backend's OpenAPI schema
// (`/api/openapi.json`) via openapi-typescript, so the contract can't drift.

export interface SessionMeta {
  id: string;
  title: string;
  turns: number;
  updated_at: number;
}

export interface ChatTurn {
  user: string;
  assistant: string;
}

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

export interface ProviderCfg {
  env: string;
  label: string;
  set: boolean;
  hint: string;
}

export interface AppConfig {
  models: {
    default: string;
    weak: string;
    mid: string;
    orchestrator: string;
    cost_mode: string;
    cascade: boolean;
    api_base: string | null;
    fallback_models: string[];
    tiers: { weak: string; mid: string; top: string };
  };
  memory: { backend: string; semantic: boolean; auto_consolidate: boolean };
  cache: { completion: boolean; prompt: boolean };
  sandbox: { mode: string; image: string };
  server: { token_set: boolean };
  providers: ProviderCfg[];
}

export interface DoctorInfo {
  has_any_key: boolean;
  configured_providers: string[];
  default_model: string;
  tiers: { weak: string; mid: string; top: string };
  memory_backend: string;
  cache: boolean;
  sandbox: string;
}

export interface MemoryItem {
  id: string;
  content: string;
  kind: string;
  provenance: string;
  source: string;
}

export interface SkillStat {
  name: string;
  kind: string;
  status: string;
  provenance: string;
  uses: number;
  successes: number;
  rate: number | null;
}

export interface CronJob {
  id: string;
  name: string;
  trigger: string;
  schedule: string;
  action: string;
  enabled: boolean;
  next_run: number | null;
  last_run: number | null;
  created_by: string;
}

export interface TaskCard {
  id: string;
  title: string;
  action: string;
  column: string;
  success: boolean | null;
  risk: string | null;
  depends_on: string[];
}

export interface ProjectState {
  id: string;
  status: string;
  iterations: number;
  plan_approved: boolean;
  pending_card_id: string | null;
  note: string;
  max_iterations: number;
}

export type Role = "user" | "assistant";

export interface Message {
  role: Role;
  content: string;
}
