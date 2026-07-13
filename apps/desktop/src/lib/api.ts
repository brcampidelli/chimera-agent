import type {
  AppConfig,
  CronJob,
  DoctorInfo,
  MemoryItem,
  ProjectState,
  SessionMeta,
  SkillStat,
  TaskCard,
  TurnReport,
  ToolEvent,
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
export const patchConfig = (updates: Record<string, string>) =>
  json<{ updated: string[] }>("/api/config", { method: "PATCH", body: JSON.stringify(updates) });

// --- Memory ---
export const getMemory = (q = "") =>
  json<MemoryItem[]>(`/api/memory${q ? `?q=${encodeURIComponent(q)}` : ""}`);
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
): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ message, session_id: sessionId, stream: true }),
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
