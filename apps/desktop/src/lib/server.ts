/**
 * Which Chimera this window is talking to.
 *
 * The app has always talked to the backend it was served from — every path in `api.ts` is
 * relative, and the bearer token arrives as a `<meta>` tag the server injects for a loopback
 * client. That is the right arrangement for the local sidecar and it is the only arrangement
 * there was, so someone running Chimera on their own VPS had no way to reach it from the app.
 *
 * This adds a second case without disturbing the first. The local server stays the default and
 * stays relative: `apiUrl("/api/x")` returns `/api/x`, byte-identical to what shipped, so nothing
 * about the local path changes — not one request, not one test.
 *
 * TWO RULES A REMOTE MUST SATISFY, AND WHY THEY ARE REFUSALS RATHER THAN WARNINGS
 *
 *   1. HTTPS. The token goes in a header on every request; over plain HTTP on the open internet
 *      that is a credential broadcast to every hop in between. A warning here would be a warning
 *      about something the user cannot see happening.
 *   2. A token. `CHIMERA_SERVER_TOKEN` is optional and unset is the local default — which means an
 *      instance exposed to the internet without one is an agent that anybody who finds the URL can
 *      run commands through. The app cannot fix that server, but it can refuse to be the thing
 *      that made it convenient.
 *
 * Loopback is exempt from both: `http://127.0.0.1:8765` is another Chimera on the same machine,
 * there is no network to eavesdrop on, and requiring a token there would break the default setup.
 */

/** A Chimera this app can talk to. `baseUrl` empty means "the one that served this page". */
export interface Server {
  readonly id: string;
  readonly name: string;
  /** Origin only, no trailing slash. Empty for the local sidecar. */
  readonly baseUrl: string;
  readonly token: string;
}

export const LOCAL: Server = { id: "local", name: "", baseUrl: "", token: "" };

const STORE_SERVERS = "chimera.servers";
const STORE_ACTIVE = "chimera.server.active";

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export function isLoopback(host: string): boolean {
  return LOOPBACK.has(host.toLowerCase());
}

/** Why this URL cannot be used, or null. The message is the reason, not "invalid input". */
export function rejectReason(raw: string, token: string): string | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return "notUrl";
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return "notHttp";
  if (isLoopback(url.hostname)) return null;
  if (url.protocol !== "https:") return "needsHttps";
  if (!token.trim()) return "needsToken";
  return null;
}

/** `https://host:port` with no trailing slash — the shape `apiUrl` concatenates against. */
export function normaliseBase(raw: string): string {
  const url = new URL(raw);
  return url.origin;
}

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    // A corrupt or unavailable store must not take the window down; the local server always works.
    return fallback;
  }
}

export function servers(): Server[] {
  return read<Server[]>(STORE_SERVERS, []);
}

export function saveServers(list: Server[]): void {
  try {
    localStorage.setItem(STORE_SERVERS, JSON.stringify(list));
  } catch {
    /* private mode / quota — the list is a convenience, not state the app needs to run */
  }
}

/**
 * The server every request goes to.
 *
 * Read on each call rather than captured once: a `LLMGateway` that photographed its settings at
 * build time is exactly the defect v0.42.0 spent a release fixing, and a module-level constant
 * here would reproduce it — switch server, and requests keep going to the old one until relaunch.
 */
export function active(): Server {
  const id = read<string>(STORE_ACTIVE, LOCAL.id);
  if (id === LOCAL.id) return LOCAL;
  return servers().find((s) => s.id === id) ?? LOCAL;
}

export function setActive(id: string): void {
  try {
    localStorage.setItem(STORE_ACTIVE, JSON.stringify(id));
  } catch {
    /* see saveServers */
  }
}

/** The URL a request goes to. Relative for local — the shipped behaviour, unchanged. */
export function apiUrl(path: string): string {
  const base = active().baseUrl;
  return base ? base + path : path;
}

/**
 * The token to send, from the active server or — for local — from the page.
 *
 * The `<meta>` tag only ever exists on a loopback-served page, because the backend refuses to put
 * a server secret into a page it is handing to a remote client. A remote server therefore cannot
 * supply its own token, and does not try: the operator gives it to their own client out of band,
 * which is the arrangement the backend's own docstring describes.
 */
export function token(): string {
  const server = active();
  if (server.baseUrl) return server.token;
  return (
    (document.querySelector('meta[name="chimera-token"]') as HTMLMetaElement | null)?.content ?? ""
  );
}

/** What asking a server who it is can tell you. Each case is one the user can act on. */
export type HandshakeResult =
  | { ok: true; version: string; appVersion: string; sameVersion: boolean }
  | { ok: false; reason: "unreachable" | "unauthorized" | "notChimera"; status?: number };

async function versionOf(url: string, bearer: string): Promise<Response> {
  return fetch(url, {
    headers: bearer ? { Authorization: `Bearer ${bearer}` } : {},
  });
}

/**
 * Ask a server who it is, before anything depends on the answer.
 *
 * The version to compare against is the LOCAL backend's, fetched relative — not a constant baked
 * into the bundle. That is not a shortcut: this interface was served by that backend (the wheel
 * bundles the UI, the native app bundles a frozen copy), so its version *is* this app's version,
 * and reading it costs one request while a build-time constant costs a way to get it wrong.
 *
 * `unreachable` covers what JavaScript cannot separate. A cross-origin request that the server did
 * not allow and a server that is simply not there both surface as the same rejected promise — the
 * browser refuses to say which, on purpose, so a page cannot use fetch to map a private network.
 * The screen therefore names both possibilities rather than guessing, and shows the origin the
 * operator has to allow.
 */
export async function handshake(baseUrl: string, bearer: string): Promise<HandshakeResult> {
  let res: Response;
  try {
    res = await versionOf(baseUrl + "/api/version", bearer);
  } catch {
    return { ok: false, reason: "unreachable" };
  }
  if (res.status === 401 || res.status === 403) return { ok: false, reason: "unauthorized" };
  if (!res.ok) return { ok: false, reason: "notChimera", status: res.status };

  let version: string;
  try {
    version = String((await res.json()).version ?? "");
  } catch {
    // A 200 that is not JSON is a login page, a proxy error page, or a different product. Reading
    // it as a success would connect the app to something that cannot answer any other call.
    return { ok: false, reason: "notChimera", status: res.status };
  }
  if (!version) return { ok: false, reason: "notChimera", status: res.status };

  let appVersion = "";
  try {
    const local = await versionOf("/api/version", localToken());
    if (local.ok) appVersion = String((await local.json()).version ?? "");
  } catch {
    // Offline from its own backend is possible and is not this connection's problem: report the
    // remote's version and leave the comparison out rather than calling a mismatch we cannot see.
  }
  return { ok: true, version, appVersion, sameVersion: !appVersion || sameMinor(version, appVersion) };
}

function localToken(): string {
  return (
    (document.querySelector('meta[name="chimera-token"]') as HTMLMetaElement | null)?.content ?? ""
  );
}

/**
 * Whether two versions can be expected to speak the same API.
 *
 * `major.minor`, because this project is pre-1.0 and its minor is where the API moves: the
 * generated client and the OpenAPI schema are regenerated together and gated on drift, so an app
 * built against 0.43 calling a 0.38 can be asking for endpoints that do not exist there.
 *
 * A mismatch warns and does not refuse. Refusing would strand the common case — a server one
 * release behind, which usually works — and the screen that would fix it is reached through the
 * connection being refused.
 */
export function sameMinor(a: string, b: string): boolean {
  const cut = (v: string) => v.split(".").slice(0, 2).join(".");
  return cut(a) === cut(b);
}
