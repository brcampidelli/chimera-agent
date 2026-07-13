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

export type Role = "user" | "assistant";

export interface Message {
  role: Role;
  content: string;
}
