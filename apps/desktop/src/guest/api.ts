/**
 * The guest's four routes, and nothing else.
 *
 * A separate client on purpose: the app's `api.ts` reaches the owner's whole server with the
 * owner's token, and a page a guest opens must not carry that surface even in code. What a guest
 * holds is a share token (`?t=` in the link), and every call here sends it as a bearer to the four
 * routes under the guest app.
 *
 * Where the guest app is depends on how the page was reached: at the root of the network listener
 * (`http://192.168…:port/`), or under `/guest/` on the owner's own server. The base is read off
 * the page's own path, so one build serves both.
 */

import { parseSseFrame, readSseFrames } from "@/lib/sse";

export interface GuestExchange {
  you: string;
  answer: string;
  tools: { name: string; ok?: boolean; observation?: string; arguments?: Record<string, unknown> }[];
  edits: { path: string; patch: string }[];
  done: Record<string, unknown> | null;
}

export interface GuestSession {
  session_id: string;
  workspace_name: string;
  exchanges: GuestExchange[];
  presence: string[];
  seq: number;
}

export interface GuestLiveFrame {
  session_seq: number;
  event: string;
  turn_id: string;
  author: string;
  payload: Record<string, unknown>;
}

/** The share token from the link, or "" when the page was opened without one. */
export function tokenFromLocation(search = window.location.search): string {
  return new URLSearchParams(search).get("t") ?? "";
}

/** `/guest` when this page is served under the owner's app, "" at the root of the listener. */
export function apiBase(pathname = window.location.pathname): string {
  return pathname.startsWith("/guest") ? "/guest" : "";
}

function headers(token: string, extra?: Record<string, string>): Record<string, string> {
  return { Authorization: `Bearer ${token}`, ...(extra ?? {}) };
}

export class GuestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function refusal(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    // not JSON
  }
  return `${res.status} ${res.statusText}`;
}

export async function getGuestSession(token: string): Promise<GuestSession> {
  const res = await fetch(`${apiBase()}/api/session`, { headers: headers(token) });
  if (!res.ok) throw new GuestError(await refusal(res), res.status);
  return (await res.json()) as GuestSession;
}

/** Send a message. The answer arrives on the live stream like everyone else's; this only waits
 *  for the server to accept the turn (the response body is the turn's own stream, drained). */
export async function sendGuestTurn(token: string, message: string, name: string): Promise<void> {
  const res = await fetch(`${apiBase()}/api/turn`, {
    method: "POST",
    headers: headers(token, { "Content-Type": "application/json" }),
    body: JSON.stringify({ message, name }),
  });
  if (!res.ok) throw new GuestError(await refusal(res), res.status);
  if (res.body) await readSseFrames(res.body, () => undefined);
}

/** Follow the conversation from `since`. Resolves with null when stopped, else with the reason. */
export async function streamGuestLive(
  token: string,
  since: number,
  name: string,
  onFrame: (frame: GuestLiveFrame) => void,
  signal?: AbortSignal,
): Promise<string | null> {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/api/live?since=${since}&name=${encodeURIComponent(name)}`, {
      headers: headers(token),
      signal,
    });
  } catch (err) {
    if (signal?.aborted) return null;
    return err instanceof Error ? err.message : "network error";
  }
  if (!res.ok || !res.body) throw new GuestError(await refusal(res), res.status);
  const cut = await readSseFrames(res.body, (raw) => {
    const frame = parseSseFrame(raw);
    if (!frame.data) return;
    try {
      const live = JSON.parse(frame.data) as Partial<GuestLiveFrame>;
      if (typeof live.session_seq !== "number") return;
      onFrame({
        session_seq: live.session_seq,
        event: String(live.event ?? frame.event),
        turn_id: String(live.turn_id ?? ""),
        author: String(live.author ?? ""),
        payload: (live.payload ?? {}) as Record<string, unknown>,
      });
    } catch {
      // a frame that is not JSON is not ours
    }
  });
  return signal?.aborted ? null : cut;
}
