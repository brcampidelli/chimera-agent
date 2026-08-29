// The API response types are GENERATED from the backend's OpenAPI schema (see `api-schema.ts`, built
// by `npm run gen:api`). Re-exporting them here means the UI can't drift from the backend: if a
// response model changes, regenerating the schema changes these types and any mismatch is a TS error.
//
// The chat stream is Server-Sent Events, not a typed HTTP body, so its event payloads (TurnReport,
// ToolEvent) and the pure UI types (Message/Role) are hand-written below — they have no OpenAPI schema.

import type { components } from "@/lib/api-schema";

type Schemas = components["schemas"];

export type SessionMeta = Schemas["SessionMetaOut"];
export type VersionInfo = Schemas["VersionOut"];
export type ChatTurn = Schemas["TurnOut"];
export type MemoryItem = Schemas["MemoryItemOut"];
export type MemoryLayers = Schemas["MemoryLayersOut"];
export type MemoryProfile = Schemas["MemoryProfileOut"];
export type SkillStat = Schemas["SkillStatOut"];
/** One curated skill card that ships in the box, as opposed to a `SkillStat`, which is one the
 *  agent distilled from the user's own runs. `body` is empty in the list and filled on detail. */
export type LibraryCard = Schemas["LibraryCardOut"];
/** An installable skill from the wider ecosystem — a pointer, not something we ship. */
export type CatalogEntry = Schemas["CatalogEntryOut"];
/** One installed on this machine: a directory of somebody else's instructions and scripts. */
export type SkillBundle = Schemas["BundleOut"];
/** A stored coding conversation's file, unparsed — the one view in which a damaged session
 *  looks damaged rather than empty. */
export type CodeSessionRaw = Schemas["CodeSessionRawOut"];
export type CronJob = Schemas["CronJobOut"];
export type TaskCard = Schemas["TaskCardOut"];
export type ProjectState = Schemas["ProjectStateOut"];
/** The orchestrator's plan for a task, before any worker runs. `decompose_spent` is the honest
 *  half: on the fan-out branch producing this plan really did cost one top-model call. */
export type HierarchyPreview = Schemas["HierarchyPreviewOut"];
/** Measured against the counterfactual, over the whole delegation ledger. Every saving field is
 *  nullable and null is NOT zero: it means the receipts carry no price to compare. */
export type DelegationSummary = Schemas["DelegationSummaryOut"];
/** The SSE frame payloads, published by GET /api/orchestration/schema so they reach the schema
 *  at all — an SSE route cannot declare a response model. */
export type OrchClassified = Schemas["ClassifiedOut"];
export type OrchDecomposed = Schemas["DecomposedOut"];
export type OrchSubtask = Schemas["SubtaskOut"];
export type OrchWorkerStarted = Schemas["WorkerStartedOut"];
export type OrchWorkerVerified = Schemas["WorkerVerifiedOut"];
export type OrchWorkerRejected = Schemas["WorkerRejectedOut"];
export type OrchFellBack = Schemas["FellBackOut"];
export type OrchDone = Schemas["HierarchyDoneOut"];
/** The crew's frames. Prefixed on the wire because a crew worker and a hierarchy worker are not
 *  the same object: this one writes files, in a checkout of its own, and a command decides
 *  whether what it wrote lands. */
export type CrewWorkerStarted = Schemas["CrewWorkerStartedOut"];
export type CrewWorkerVerified = Schemas["CrewWorkerVerifiedOut"];
export type CrewWorkerRejected = Schemas["CrewWorkerRejectedOut"];
export type CrewDone = Schemas["CrewDoneOut"];
export type ProviderCfg = Schemas["ProviderOut"];
/** A provider's rotation pool. Carries hints and positions — never a key. */
export type PoolCfg = Schemas["PoolOut"];
export type PoolWrite = Schemas["PoolWriteOut"];
export type AppConfig = Schemas["ConfigOut"];
export type AgentIdentity = Schemas["AgentIdentityOut"];
/** An agent you dispatch work to, as opposed to the one you converse with. */
export type AgentDef = Schemas["AgentDefOut"];
export type DoctorInfo = Schemas["DoctorOut"];
export type ConfigTest = Schemas["ConfigTestOut"];
export type UsageSummary = Schemas["UsageSummaryOut"];
export type RunReceipt = Schemas["RunReceiptOut"];
export type AttemptReceipt = Schemas["AttemptReceiptOut"];
export type AgentsBatch = Schemas["AgentsBatchOut"];
export type AgentResult = Schemas["AgentResultOut"];
export type PlanResult = Schemas["PlanOut"];
export type FileDiff = Schemas["FileDiffOut"];
export type FsTree = Schemas["FsTreeOut"];
export type FsNode = Schemas["FsNodeOut"];
export type FsFile = Schemas["FsFileOut"];
export type FsFileWritten = Schemas["FsFileWrittenOut"];
export type SearchResult = Schemas["SearchOut"];
export type SearchHit = Schemas["SearchHitOut"];
export type Resources = Schemas["ResourcesOut"];
export type DiagnosticsResult = Schemas["DiagnosticsOut"];
export type LspDiagnostic = Schemas["DiagnosticOut"];
export type InlineCompletion = Schemas["CompletionOut"];
export type CompletionAcceptance = Schemas["AcceptanceOut"];
/** What the configured Ollama has pulled. `reachable` and an empty `models` are different answers. */
export type OllamaModels = Schemas["OllamaModelsOut"];
/** The models a turn may name, from every catalogue this install can reach. `reason` is set NEXT TO
 *  a non-empty list when the remote index failed and only the curated one answered. */
export type ModelListing = Schemas["ModelsOut"];
/** One pickable model. `tools: null` is "we were not told", which is not `false` — a turn without
 *  tool calling can only describe an edit, and the UI has to say which of the two it knows. */
export type ModelOption = Schemas["ModelOptionOut"];
export type GitStatus = Schemas["GitStatusOut"];
export type GitFile = Schemas["GitFileOut"];
export type GitDiff = Schemas["GitDiffOut"];
export type GitCommitResult = Schemas["GitCommitOut"];
export type GitRevertResult = Schemas["GitRevertOut"];
export type GitInitResult = Schemas["GitInitOut"];
export type InjectionReport = Schemas["InjectionReportOut"];
export type GovernanceAudit = Schemas["GovernanceAuditOut"];
export type SandboxState = Schemas["SandboxStateOut"];
export type ToolInfo = Schemas["ToolInfoOut"];
export type Tools = Schemas["ToolsOut"];
export type Maturity = Schemas["MaturityOut"];
export type MaturitySurface = Schemas["MaturitySurfaceOut"];
export type Benchmarks = Schemas["BenchmarksOut"];
export type BenchmarkLift = Schemas["BenchmarkLiftOut"];
export type BenchmarkExternal = Schemas["BenchmarkExternalOut"];
export type McpServer = Schemas["McpServerOut"];
export type McpServers = Schemas["McpServersOut"];
export type McpTool = Schemas["McpToolOut"];
export type McpTest = Schemas["McpTestOut"];

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
  /**
   * This turn ran through the fusion panel, which means it could NOT use tools.
   *
   * `FusionEngine.complete` drops the tool schemas, so the agent finishes in one step having touched
   * nothing and answers from the prompt alone. Ask it to read a file and it describes a file it
   * never opened — with the authority of three models agreeing. From the outside that is
   * indistinguishable from a turn that legitimately needed no tool: both report zero tool calls.
   * This flag is the entire difference.
   */
  fused?: boolean;
}

export interface ToolEvent {
  name: string;
  ok: boolean;
}

export type Role = "user" | "assistant";

export interface Message {
  role: Role;
  content: string;
  /** This answer came from the fusion panel, which cannot call tools — see {@link TurnReport.fused}.
   *  Carried on the MESSAGE rather than read from the latest report, because the warning has to stay
   *  attached to the answer it is about once the next turn has moved on. */
  fused?: boolean;
}
