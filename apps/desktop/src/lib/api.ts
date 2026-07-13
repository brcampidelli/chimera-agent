import type { SessionMeta, TurnReport, ToolEvent } from "@/lib/types";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const listSessions = () => json<SessionMeta[]>("/api/sessions");
export const getSession = (id: string) =>
  json<{ id: string; turns: { user: string; assistant: string }[] }>(`/api/sessions/${id}`);
export const deleteSession = (id: string) =>
  json<{ deleted: boolean }>(`/api/sessions/${id}`, { method: "DELETE" });

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
      headers: { "Content-Type": "application/json" },
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
