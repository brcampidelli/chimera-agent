import type {
  AgentsBatch,
  AppConfig,
  ConfigTest,
  CronJob,
  DoctorInfo,
  FsFile,
  FsFileWritten,
  FsTree,
  GitCommitResult,
  GitDiff,
  GitRevertResult,
  GitStatus,
  Benchmarks,
  GovernanceAudit,
  InjectionReport,
  Maturity,
  McpServers,
  McpTest,
  MemoryItem,
  MemoryLayers,
  MemoryProfile,
  PlanResult,
  ProjectState,
  RunReceipt,
  Screenshot,
  SessionMeta,
  SkillStat,
  TaskCard,
  TurnReport,
  ToolEvent,
  Tools,
  UsageSummary,
  VersionInfo,
} from "@/lib/types";

// The backend injects the bearer token into the page (as a meta tag) only for a loopback client, when
// CHIMERA_SERVER_TOKEN is set. We forward it so the guarded endpoints work; when no token is set the
// meta is absent and nothing is sent (the localhost default is unauthenticated).
const SERVER_TOKEN =
  (document.querySelector('meta[name="chimera-token"]') as HTMLMetaElement | null)?.content ?? "";

function authHeaders(extra?: HeadersInit): HeadersInit {
  const base: Record<string, string> = { "Content-Type": "application/json" };
  if (SERVER_TOKEN) base.Authorization = `Bearer ${SERVER_TOKEN}`;
  return { ...base, ...(extra ?? {}) };
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: authHeaders(init?.headers) });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const listSessions = () => json<SessionMeta[]>("/api/sessions");
export const getSession = (id: string) =>
  json<{ id: string; turns: { user: string; assistant: string }[] }>(`/api/sessions/${id}`);
export const deleteSession = (id: string) =>
  json<{ deleted: boolean }>(`/api/sessions/${id}`, { method: "DELETE" });

// The running version + an HONEST update signal: `update_available` is true ONLY when GitHub confirms
// a strictly-newer release. Offline / any error → {latest:null, update_available:false} (never a false
// "update available"). The backend caches the GitHub result, so polling this is cheap.
export const getVersion = () => json<VersionInfo>("/api/version");

export const getConfig = () => json<AppConfig>("/api/config");
export const getDoctor = () => json<DoctorInfo>("/api/doctor");
export const getUsage = () => json<UsageSummary>("/api/usage");
export const getRuns = () => json<RunReceipt[]>("/api/runs");
export const getGovernanceInjection = () =>
  json<InjectionReport>("/api/governance/injection");
export const getGovernanceAudit = () => json<GovernanceAudit>("/api/governance/audit");
export const getTools = () => json<Tools>("/api/tools");

// --- Filesystem (read-only tree + file viewer for the Code screen) ---
// Both are path-scoped server-side to the workspace; a `..` escape is a 400, a binary/dir is an
// honest note (never a 500). The tree is lazy — one directory level per call.
export const getFsTree = (workspace?: string | null, path = "") => {
  const params = new URLSearchParams({ path });
  if (workspace) params.set("workspace", workspace);
  return json<FsTree>(`/api/fs/tree?${params.toString()}`);
};
export const getFsFile = (workspace: string | null | undefined, path: string) => {
  const params = new URLSearchParams({ path });
  if (workspace) params.set("workspace", workspace);
  return json<FsFile>(`/api/fs/file?${params.toString()}`);
};
// Editable-viewer save (PUT): atomic + newline-preserving + size-capped server-side. A `..` escape or
// oversize content is a 400. Returns the bytes actually written (may exceed content length on a CRLF file).
export const saveFile = (workspace: string | null | undefined, path: string, content: string) =>
  json<FsFileWritten>("/api/fs/file", {
    method: "PUT",
    body: JSON.stringify({ workspace: workspace || null, path, content }),
  });
// --- Git (status / diff / commit / scoped revert for the Code screen's git panel) ---
// All gate on `is_git_repo` server-side: a non-repo (or git-missing) folder returns the honest
// {is_repo: false} empty-state, never a 500. Commit stages EXPLICIT paths (never `add -A`); revert is
// git-backed and scoped to the passed paths only (never workspace-wide).
export const getGitStatus = (workspace?: string | null) => {
  const params = new URLSearchParams();
  if (workspace) params.set("workspace", workspace);
  const qs = params.toString();
  return json<GitStatus>(`/api/git/status${qs ? `?${qs}` : ""}`);
};
export const getGitDiff = (workspace: string | null | undefined, path?: string | null, staged = false) => {
  const params = new URLSearchParams();
  if (workspace) params.set("workspace", workspace);
  if (path) params.set("path", path);
  if (staged) params.set("staged", "true");
  const qs = params.toString();
  return json<GitDiff>(`/api/git/diff${qs ? `?${qs}` : ""}`);
};
export const gitCommit = (workspace: string | null | undefined, message: string, paths: string[]) =>
  json<GitCommitResult>("/api/git/commit", {
    method: "POST",
    body: JSON.stringify({ workspace: workspace || null, message, paths }),
  });
export const gitRevert = (workspace: string | null | undefined, paths: string[]) =>
  json<GitRevertResult>("/api/git/revert", {
    method: "POST",
    body: JSON.stringify({ workspace: workspace || null, paths }),
  });

export const getMaturity = () => json<Maturity>("/api/maturity");
// The agent's REAL recorded benchmark numbers (the promising weak-model lift + the humbling external
// Terminal-Bench), each carrying its n/CI/significance. Read-only from the shipped snapshot; an
// unavailable snapshot returns {available:false}, never a 500.
export const getBenchmarks = () => json<Benchmarks>("/api/benchmarks");
export const patchConfig = (updates: Record<string, string>) =>
  json<{ updated: string[] }>("/api/config", { method: "PATCH", body: JSON.stringify(updates) });
// The ONLY honest "key works" call: makes a real 1-token completion server-side. `ok:true` means it
// authenticated; otherwise `error` carries a short, secret-free message. Used by the onboarding wizard.
export const testProviderKey = (model?: string) =>
  json<ConfigTest>("/api/config/test", {
    method: "POST",
    body: JSON.stringify({ model: model ?? null }),
  });

// --- Memory ---
export const getMemory = (q = "") =>
  json<MemoryItem[]>(`/api/memory${q ? `?q=${encodeURIComponent(q)}` : ""}`);
export const getMemoryLayers = () => json<MemoryLayers>("/api/memory/layers");
// The agent's learned picture of you. The UI could WRITE persona facts (addMemory with
// kind: "persona") but had no way to read them back — you fed a store you could never inspect.
export const getMemoryProfile = () => json<MemoryProfile>("/api/memory/profile");
export const addMemory = (content: string, kind: string) =>
  json<{ status: string; item: MemoryItem }>("/api/memory", {
    method: "POST",
    body: JSON.stringify({ content, kind }),
  });
export const deleteMemory = (id: string) =>
  json<{ deleted: boolean }>(`/api/memory/${id}`, { method: "DELETE" });

// --- Skills ---
export const getSkills = () =>
  json<{ stats: SkillStat[]; retirement_candidates: string[] }>("/api/skills");
export const approveSkill = (name: string) =>
  json<{ approved: boolean }>(`/api/skills/${name}/approve`, { method: "POST" });
export const retireSkill = (name: string) =>
  json<{ retired: boolean }>(`/api/skills/${name}/retire`, { method: "POST" });

// --- Messaging (reach the user on Discord/Telegram) ---
export type MessagingPlatform = { configured: boolean; running: boolean; error: string | null };
export type MessagingStatus = Record<string, MessagingPlatform>;
export const getMessaging = () => json<MessagingStatus>("/api/messaging");
export const startMessaging = (platform: string) =>
  json<MessagingStatus>(`/api/messaging/${platform}/start`, { method: "POST" });
export const stopMessaging = (platform: string) =>
  json<MessagingStatus>(`/api/messaging/${platform}/stop`, { method: "POST" });

// --- Cron ---
export const getCron = () => json<CronJob[]>("/api/cron");
export const createCron = (body: { name: string; schedule: string; action: string }) =>
  json<CronJob>("/api/cron", { method: "POST", body: JSON.stringify(body) });
export const enableCron = (id: string) => json<CronJob>(`/api/cron/${id}/enable`, { method: "POST" });
export const disableCron = (id: string) =>
  json<CronJob>(`/api/cron/${id}/disable`, { method: "POST" });
export const deleteCron = (id: string) =>
  json<{ deleted: boolean }>(`/api/cron/${id}`, { method: "DELETE" });

// --- MCP / Integrations ---
// Config reads/writes are cheap and NEVER connect (env values are never returned). `testMcpServer` is
// the ONLY connecting call — a real stdio connect + tool enumeration, the sole honest "connected" proof.
export const getMcpServers = () => json<McpServers>("/api/mcp");
export const addMcpServer = (body: {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
}) => json<McpServers>("/api/mcp", { method: "POST", body: JSON.stringify(body) });
export const removeMcpServer = (name: string) =>
  json<{ deleted: boolean }>(`/api/mcp/${encodeURIComponent(name)}`, { method: "DELETE" });
export const testMcpServer = (name: string) =>
  json<McpTest>(`/api/mcp/${encodeURIComponent(name)}/test`, { method: "POST" });

// --- Tasks (kanban + projects, HITL) ---
export const getKanban = () => json<Record<string, TaskCard[]>>("/api/kanban");
export const getProjects = () => json<ProjectState[]>("/api/projects");
export const getProject = (id: string) =>
  json<{ state: ProjectState; columns: Record<string, TaskCard[]> }>(`/api/projects/${id}`);
export const approveProject = (id: string, card?: string) =>
  json<ProjectState>(`/api/projects/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ card: card ?? null }),
  });
export const denyProject = (id: string, card: string) =>
  json<ProjectState>(`/api/projects/${id}/deny`, { method: "POST", body: JSON.stringify({ card }) });

export interface StreamHandlers {
  onSession?: (id: string) => void;
  onToken?: (text: string) => void;
  onTool?: (t: ToolEvent) => void;
  onDone?: (r: TurnReport) => void;
  onError?: (msg: string) => void;
}

/** Stream one chat turn. The API's SSE lives on a POST, so we read the response body ourselves
 *  (EventSource is GET-only) and parse `event:`/`data:` frames as they arrive. */
export async function streamChat(
  message: string,
  sessionId: string | null,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  fuse = false,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ message, session_id: sessionId, stream: true, fuse }),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatch(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatch(buffer, handlers);
}

function dispatch(frame: string, h: StreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "session") h.onSession?.(payload.session_id as string);
  else if (event === "token") h.onToken?.(payload.text as string);
  else if (event === "tool") h.onTool?.(payload as unknown as ToolEvent);
  else if (event === "done") h.onDone?.(payload as unknown as TurnReport);
  else if (event === "error") h.onError?.(payload.message as string);
}

// --- Runs (in-app autonomous run trigger, streamed) ---

export interface RunRequestInput {
  task: string;
  verify?: string | null;
  workspace?: string | null;
  max_attempts?: number;
  // An approved/edited plan (raw text from the preview). When set, the run uses THIS plan verbatim
  // instead of re-planning. Omitted = the run plans for itself (current behaviour).
  plan?: string | null;
  // The worker's model slug (omitted / null = the configured default) and the routing mode.
  model?: string | null;
  fuse?: boolean;
  cascade?: boolean;
  // How far the agent may reach and when it stops to ask. The server resolves it into tool denials
  // and pause flags, and mints a thread when a pause is asked for and none was sent.
  posture?: { reach: Reach; approval: Approval } | null;
  // Which tier each ROLE draws from. Not a claim that routing helps — see bench/role_routing.
  profile?: Profile | null;
}

/** Preview a plan for a task: runs ONLY the planner (a single model call) — NO edits, NO tools, no
 *  workspace changes. Returns the concrete steps so the user can review/edit before approving a run.
 *  A model hiccup degrades to empty steps + a `note`, never an HTTP error. */
export const getPlan = (workspace: string | null | undefined, task: string) =>
  json<PlanResult>("/api/plan", {
    method: "POST",
    body: JSON.stringify({ task, workspace: workspace || null }),
  });

/** Capture a browser screenshot verification artifact of `url`: the headless browser navigates there
 *  and a full-page PNG is stored server-side, fetched back via `/api/artifacts/{id}`. It's an HONEST
 *  capture of whatever the URL renders — NOT a claim the agent verified anything. If the browser
 *  runtime is missing (or navigation fails), returns `{ok:false, error}` (the honest install hint),
 *  never a fake image and never an HTTP error. */
export const captureScreenshot = (url: string, workspace?: string | null) =>
  json<Screenshot>("/api/verify/screenshot", {
    method: "POST",
    body: JSON.stringify({ url, workspace: workspace || null }),
  });

/** One live progress frame from the run loop (an AgentEvent, serialized). `kind` picks the shape. */
export interface RunEvent {
  kind: string;
  text: string;
  index?: number;
  max_attempts?: number;
  success?: boolean;
  detail?: string;
  // `kind === "edit"`: the REAL unified diff of a file the agent just changed this step (never
  // fabricated — read from the file on disk before/after the write-tool call).
  path?: string;
  patch?: string;
}

/** The terminal `done` payload of a run. */
export interface RunDone {
  success: boolean;
  answer: string;
  attempts: number;
  // "cancelled" when a cooperative Stop ended the run between attempts; "" for an ordinary finish.
  stopped_reason?: string;
}

export interface RunStreamHandlers {
  onEvent?: (e: RunEvent) => void;
  onDone?: (d: RunDone) => void;
  onError?: (msg: string) => void;
  // The run's id, delivered on the first `run` frame — the handle for POST /api/runs/{id}/cancel.
  onRunId?: (id: string) => void;
  // The run stopped for a human verdict. Arrives INSTEAD of `onDone`.
  onPaused?: (p: PausedRun) => void;
}

/** Trigger an autonomous run and stream its live progress. Mirrors {@link streamChat}: the API's SSE
 *  lives on a POST, so we read the response body ourselves and parse `event`/`done`/`error` frames.
 *  This WRITES files and runs the verify command in the workspace (same as `chimera solve`). */
export async function streamRun(
  req: RunRequestInput,
  handlers: RunStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/runs", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchRun(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchRun(buffer, handlers);
}

function dispatchRun(frame: string, h: RunStreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "run") h.onRunId?.(payload.run_id as string);
  else if (event === "event") h.onEvent?.(payload as unknown as RunEvent);
  else if (event === "done") h.onDone?.(payload as unknown as RunDone);
  else if (event === "paused") h.onPaused?.(payload as unknown as PausedRun);
  else if (event === "error") h.onError?.(payload.message as string);
}

// --- Code conversation (turns that edit, streamed) ---

export interface CodeTurnInput {
  message: string;
  // The conversation this turn belongs to. Omit to start a new one; the id arrives in the first
  // frame and every later turn MUST send it back, or each message starts from nothing.
  session_id?: string | null;
  workspace?: string | null;
  model?: string | null;
  stream?: boolean;
  // The file open in the viewer. Two real effects server-side: it focuses which AGENTS.md files
  // apply, and it is what a compaction restores. Only the path travels — the agent re-reads it.
  open_file?: string | null;
  max_steps?: number | null;
  context_budget?: number | null;
  repo_map?: boolean;
  explorer?: boolean;
  posture?: { reach: Reach; approval: Approval } | null;
  profile?: Profile | null;
}

/** One tool call, as it happens. `arguments` and `observation` arrive already clipped server-side
 *  and SAY so when they were — the UI must never present a truncated observation as complete. */
export interface CodeToolEvent {
  name: string;
  arguments: Record<string, string>;
  ok: boolean;
  observation: string;
}

/** The terminal `done` payload of a coding turn — what it did, what it cost, how close to the wall. */
export interface CodeTurnDone {
  answer: string;
  steps: number;
  stopped_reason: string;
  tool_names: string[];
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  usd: number | null;
  // The largest prompt this turn built. The number that says whether raising max_steps is safe —
  // shown rather than hidden, because a ceiling raised without seeing its cost is a trap.
  context_peak_tokens: number;
  route_meta: Record<string, unknown> | null;
}

export interface CodeTurnHandlers {
  onSession?: (id: string) => void;
  onToken?: (text: string) => void;
  onTool?: (e: CodeToolEvent) => void;
  onEdit?: (path: string, patch: string) => void;
  onDone?: (d: CodeTurnDone) => void;
  onError?: (msg: string) => void;
}

/** Send one turn of a coding conversation and stream it. Mirrors {@link streamRun}: the SSE lives
 *  on a POST, so we read the body and parse the frames ourselves.
 *
 *  This WRITES files in the workspace, like the run trigger — the difference is not permission, it
 *  is that a turn is a conversation step (fast, no verify-or-revert) while a run is a transaction. */
export async function streamCodeTurn(
  req: CodeTurnInput,
  handlers: CodeTurnHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/code/turn", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchCodeTurn(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchCodeTurn(buffer, handlers);
}

function dispatchCodeTurn(frame: string, h: CodeTurnHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    // A token delta can legitimately be whitespace, so this one is NOT trimmed — trimming it turns
    // streamed prose into a wall of runtogetherwords.
    else if (line.startsWith("data:")) data += line.slice(5).replace(/^ /, "");
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "session") h.onSession?.(payload.session_id as string);
  else if (event === "token") h.onToken?.(payload.text as string);
  else if (event === "tool") h.onTool?.(payload as unknown as CodeToolEvent);
  else if (event === "edit") h.onEdit?.(payload.path as string, payload.patch as string);
  else if (event === "done") h.onDone?.(payload as unknown as CodeTurnDone);
  else if (event === "error") h.onError?.(payload.message as string);
}

// --- Posture (how far the agent reaches, and when it stops to ask) ---

export type Reach = "read_only" | "workspace" | "workspace_shell";
export type Approval = "always" | "suspicious" | "never";

/** What the chosen posture means on THIS machine, right now. Structured rather than prose so the
 *  sentence can be rendered in nine languages — a server that returned English would make this the
 *  one untranslated line on the screen. */
export interface PostureFacts {
  writes: "nothing" | "workspace";
  workspace: string;
  shell: "none" | "isolated" | "host" | "asks" | "refused";
  pauses: "always" | "tainted" | "never";
  // True when the shell would run on this machine while the config asked for a container. The one
  // case where the honest answer contradicts the user's setup, so it is never folded into `shell`.
  fell_back_to_host: boolean;
}

/** Ask what a posture would mean, without committing to it.
 *
 *  A POST, and never cached: it reports the LIVE state of the sandbox, so a Docker daemon that died
 *  since the last call has to change the answer rather than be served from a cache. */
export const getPostureFacts = (
  reach: Reach,
  approval: Approval,
  workspace?: string | null,
) =>
  json<PostureFacts>("/api/code/posture", {
    method: "POST",
    body: JSON.stringify({ reach, approval, workspace: workspace || null }),
  });

// --- Roles (which model does which job) ---

export type Profile = "economy" | "balanced" | "max";

/** One model slug per role. `verify` is absent rather than nullable: offering a field would imply a
 *  choice exists, and the whole value of an executable verifier is that there isn't one. */
export interface RoleModels {
  explore: string | null;
  plan: string | null;
  edit: string | null;
  review: string | null;
  // Only the two TOOL-FREE turns can be fused. A `fuse` on the coding loop would never fire — the
  // router sends any turn carrying tool schemas to a single model — and would report that it had.
  fuse_plan: boolean;
  fuse_review: boolean;
}

/** The concrete slugs a profile resolves to. Asked of the server rather than mirrored here: the
 *  tiers honour the user's cost mode and per-tier settings, and a second copy of that resolution in
 *  TypeScript would display a model the run does not actually use. */
export const getRoleModels = (profile: Profile) =>
  json<RoleModels>("/api/code/roles", {
    method: "POST",
    body: JSON.stringify({ profile }),
  });

// --- "Was it worth it?" (what each profile cost, and what it got) ---

export interface ProfileWorth {
  profile: string | null;
  runs: number;
  passed: number;
  reverted: number;
  // Runs whose SUCCESSFUL attempt changed no file — the empty-patch failure, kept apart from
  // `passed` so a configuration cannot look good at the thing it is bad at.
  unproductive: number;
  attempts_total: number;
  // Null when ANY run in the group had an unknown cost. `usd_known_runs` is the denominator:
  // without it, a null is indistinguishable from "no data" and a number from "all of it".
  usd_total: number | null;
  usd_known_runs: number;
}

export interface WorthReport {
  profiles: ProfileWorth[];
  total_runs: number;
  readable_n: number;
  any_readable: boolean;
}

/** What each configuration actually cost and got, over the runs that really happened here.
 *
 *  The groups arrive sorted BY NAME, never by outcome — they are observational (different tasks,
 *  different days, no randomisation), and ordering them by pass rate would read as a ranking these
 *  numbers cannot support. The comparison that can is the paired A/B in bench/role_routing. */
export const getWorth = () => json<WorthReport>("/api/code/worth");

/** Forget a coding conversation. An unknown id is `{ok:false}`, not an error — that is exactly the
 *  state a second click on Clear hits. */
export const deleteCodeSession = (sessionId: string) =>
  json<{ ok: boolean }>(`/api/code/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });

/** A run that stopped before finalizing and is waiting for a human verdict. Arrives on the stream's
 *  `paused` frame INSTEAD of `done` — a pause is not a verdict, and treating it as a failed run
 *  would quietly throw away work that is sitting there to be released. */
export interface PausedRun {
  thread_id: string;
  answer: string;
  tainted?: boolean;
}

/** Every run parked awaiting a verdict, including ones this window never witnessed. */
export const getPausedRuns = () => json<PausedRun[]>("/api/runs/paused");

/** The four HITL actions, mirroring the core's LangGraph `HumanInterrupt` envelope. */
export type HitlAction = "accept" | "edit" | "respond" | "ignore";

/** Record a verdict on a paused run. This does NOT conclude it: every action needs the run to be
 *  resumed (POST /api/runs with the same thread_id), which is where the answer is finalized. Only
 *  `respond` spends another attempt — `retries` says so, so the UI can warn before committing it. */
export const respondRun = (
  threadId: string,
  action: HitlAction,
  body: { answer?: string; feedback?: string } = {},
) =>
  json<{ ok: boolean; resume_required: boolean; retries: boolean }>(
    `/api/runs/${encodeURIComponent(threadId)}/respond`,
    { method: "POST", body: JSON.stringify({ action, ...body }) },
  );

/** Cooperatively cancel an in-flight run: the loop halts BEFORE its next attempt (an in-flight model
 *  step can't be interrupted). A finished/unknown id is a no-op {ok:false}, never an error. */
export const cancelRun = (runId: string) =>
  json<{ ok: boolean }>(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });

// --- Agents (the Agent Manager: a parallel batch of isolated autonomous runs, streamed) ---

export interface AgentTaskInput {
  task: string;
  // A shell command that judges THIS task (exit 0 = pass), run in the task's isolated worktree.
  verify?: string | null;
}

export interface AgentsRequestInput {
  tasks: AgentTaskInput[];
  workspace?: string | null;
  max_workers?: number;
  // The worker's model slug (omitted / null = the configured default) and the routing mode.
  model?: string | null;
  fuse?: boolean;
  cascade?: boolean;
}

/** The `start` frame: the batch is underway (the task list + the resolved workspace). */
export interface AgentsStart {
  tasks: string[];
  workspace: string;
  max_workers: number;
}

/** One live progress frame, TAGGED with `index` so the board routes it to the right task card. It's a
 *  run `event` (an AgentEvent, serialized) — same shape as {@link RunEvent}, which already carries the
 *  optional `index`. */
export type AgentTaggedEvent = RunEvent & { index: number };

export interface AgentsStreamHandlers {
  onStart?: (s: AgentsStart) => void;
  onEvent?: (e: AgentTaggedEvent) => void;
  onBatchDone?: (b: AgentsBatch) => void;
  onError?: (msg: string) => void;
  // The batch's id, delivered on the first `batch` frame — the handle for
  // POST /api/agents/{id}/cancel (mirrors {@link RunStreamHandlers.onRunId}).
  onBatchId?: (id: string) => void;
}

/** Trigger a parallel batch of autonomous runs (each in its OWN git worktree) and stream progress.
 *  Mirrors {@link streamRun}: the SSE lives on a POST, so we read the body and parse the frames —
 *  `start`, per-task tagged `event`s, a terminal `batch_done`, or `error`. This WRITES files and runs
 *  each verify command in the workspace (same capability as `chimera solve-batch`). Isolation is REAL
 *  only in a git repo; `batch_done.is_repo === false` means the tasks ran in-place, without it. */
export async function streamAgents(
  req: AgentsRequestInput,
  handlers: AgentsStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/agents", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchAgents(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchAgents(buffer, handlers);
}

function dispatchAgents(frame: string, h: AgentsStreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "batch") h.onBatchId?.(payload.batch_id as string);
  else if (event === "start") h.onStart?.(payload as unknown as AgentsStart);
  else if (event === "event") h.onEvent?.(payload as unknown as AgentTaggedEvent);
  else if (event === "batch_done") h.onBatchDone?.(payload as unknown as AgentsBatch);
  else if (event === "error") h.onError?.(payload.message as string);
}

/** Cooperatively cancel tasks in an in-flight batch: `index` stops just that task, omitting it (null)
 *  stops every task. Each task halts BEFORE its next attempt (an in-flight model step can't be
 *  interrupted). A finished/unknown batch is a no-op {ok:false, cancelled:0}, never an error. */
export const cancelAgents = (batchId: string, index?: number | null) =>
  json<{ ok: boolean; cancelled: number }>(`/api/agents/${encodeURIComponent(batchId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({ index: index ?? null }),
  });

// --- Command runner (workspace-scoped, streamed; fresh subprocess per command — NOT a terminal) ---

export interface ExecRequestInput {
  command: string;
  workspace?: string | null;
  cwd?: string;
  timeout?: number;
}

export interface ExecStreamHandlers {
  onLine?: (text: string) => void;
  onExit?: (code: number) => void;
  onError?: (msg: string) => void;
}

/** Run one command and stream its combined stdout+stderr line by line, then the exit code. Mirrors
 *  {@link streamRun}: the SSE lives on a POST, so we read the body and parse `line`/`exit` frames.
 *  Each call is a FRESH subprocess on the host (or the configured sandbox) — cwd/env don't persist. */
export async function streamExec(
  req: ExecRequestInput,
  handlers: ExecStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/fs/exec", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchExec(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchExec(buffer, handlers);
}

function dispatchExec(frame: string, h: ExecStreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "line") h.onLine?.(payload.text as string);
  else if (event === "exit") h.onExit?.(payload.code as number);
  else if (event === "error") h.onError?.(payload.message as string);
}
