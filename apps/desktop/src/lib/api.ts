import type {
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
  ProjectState,
  RunReceipt,
  SessionMeta,
  SkillStat,
  TaskCard,
  TurnReport,
  ToolEvent,
  Tools,
  UsageSummary,
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

// --- Cron ---
export const getCron = () => json<CronJob[]>("/api/cron");
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
}

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
}

export interface RunStreamHandlers {
  onEvent?: (e: RunEvent) => void;
  onDone?: (d: RunDone) => void;
  onError?: (msg: string) => void;
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
  if (event === "event") h.onEvent?.(payload as unknown as RunEvent);
  else if (event === "done") h.onDone?.(payload as unknown as RunDone);
  else if (event === "error") h.onError?.(payload.message as string);
}

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
