/**
 * The two requests that carry a file, and the one header they must not carry.
 *
 * These endpoints were broken in the shipped app and nothing noticed, because the cover was in the
 * wrong place: every component test mocks `@/lib/api` wholesale, so `uploadAttachment` was a stub
 * and the real `fetch` — the thing with the bug in it — never ran. Attaching a file and dictating a
 * message both answered `422 field required` about a file that was in the request all along.
 *
 * So these tests stub `fetch` instead of the module, and assert on what would actually go over the
 * wire. The FormData body is the whole reason: only the browser knows the multipart boundary it
 * generated, and it writes `Content-Type` for you exactly when you have not written one yourself.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Api = typeof import("@/lib/api");

let calls: { url: string; init: RequestInit }[];

/** Import `api.ts` fresh, having decided what the injected `<meta name="chimera-token">` says.
 *
 *  Fresh matters: the token is read once at module load, so a test that wants to see it forwarded
 *  has to put it in the DOM before the module is evaluated. */
async function load(token?: string): Promise<Api> {
  document.head.innerHTML = token ? `<meta name="chimera-token" content="${token}">` : "";
  vi.resetModules();
  return import("@/lib/api");
}

function ok(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return ok({ id: "a1", name: "n", kind: "document", chars: 1, note: "", text: "" });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.head.innerHTML = "";
  delete window.__CHIMERA_SERVER_TOKEN__;
});

const header = (init: RequestInit, name: string) => new Headers(init.headers).get(name);

describe("a request that carries a file", () => {
  it("lets the browser set Content-Type on an attachment, so the boundary survives", async () => {
    const api = await load();
    await api.uploadAttachment(new File(["hello"], "a.txt", { type: "text/plain" }));

    expect(calls[0].url).toBe("/api/attachments");
    // Any Content-Type here means multipart bytes arriving labelled as something else, with no
    // boundary for the server to split them on — which is a 422 naming the field that was present.
    expect(header(calls[0].init, "content-type")).toBeNull();
    expect(calls[0].init.body).toBeInstanceOf(FormData);
  });

  it("does the same for dictated audio", async () => {
    const api = await load();
    await api.transcribe(new Blob(["x"], { type: "audio/webm" }));

    expect(calls[0].url).toBe("/api/transcribe");
    expect(header(calls[0].init, "content-type")).toBeNull();
    expect(calls[0].init.body).toBeInstanceOf(FormData);
  });

  it("still authenticates — dropping Content-Type must not drop the token", async () => {
    // Both endpoints are behind the guard. A fix that made them anonymous would trade a 422 for a
    // 401 on any instance that sets CHIMERA_SERVER_TOKEN, and look just as broken.
    const api = await load("tok-123");
    await api.uploadAttachment(new File(["hello"], "a.txt"));
    await api.transcribe(new Blob(["x"]));

    expect(header(calls[0].init, "authorization")).toBe("Bearer tok-123");
    expect(header(calls[1].init, "authorization")).toBe("Bearer tok-123");
    expect(header(calls[0].init, "content-type")).toBeNull();
  });

  it("authenticates the live-session SSE stream and never puts the token in its URL", async () => {
    // A GET stream with its own query string: the one place a token would be tempting to append.
    const api = await load("tok-123");
    await api.streamSessionLive("s1", 0, () => {});

    expect(calls[0].url).toBe("/api/code/sessions/s1/live?since=0&name=owner");
    expect(header(calls[0].init, "authorization")).toBe("Bearer tok-123");
    expect(calls[0].url).not.toContain("tok-123");
  });

  it("authenticates a run SSE stream and never puts the token in its URL", async () => {
    const api = await load("tok-123");
    await api.streamRun({ task: "inspect" }, {});

    expect(calls[0].url).toBe("/api/runs");
    expect(header(calls[0].init, "authorization")).toBe("Bearer tok-123");
    expect(calls[0].url).not.toContain("tok-123");
  });

  it("sends no Authorization at all when no token is configured", async () => {
    // The localhost default is unauthenticated; an empty Bearer would be a header claiming a
    // credential that does not exist.
    const api = await load();
    await api.uploadAttachment(new File(["hello"], "a.txt"));
    expect(header(calls[0].init, "authorization")).toBeNull();
  });
});

describe("a request that carries JSON", () => {
  it("keeps Content-Type: application/json", async () => {
    // The guard against overcorrecting: the fix is about FormData bodies, not about every request.
    const api = await load("tok-123");
    await api.getConfig();

    expect(header(calls[0].init ?? {}, "content-type")).toBe("application/json");
    expect(header(calls[0].init ?? {}, "authorization")).toBe("Bearer tok-123");
  });
});

/**
 * The native desktop shell hands the per-launch sidecar token to the page through Tauri's
 * initialization script (`window.__CHIMERA_SERVER_TOKEN__`), not through a `<meta>` the backend
 * injects — the sidecar deliberately stops injecting one, so a local process cannot read the secret
 * off the public index page. These hold the client to reading the new place, on every path.
 */
describe("the token the native shell set", () => {
  it("is sent as Bearer when it is the only token the page has", async () => {
    const api = await load();
    window.__CHIMERA_SERVER_TOKEN__ = "native-tok";
    await api.getConfig();

    expect(header(calls[0].init ?? {}, "authorization")).toBe("Bearer native-tok");
    expect(calls[0].url).not.toContain("native-tok");
  });

  it("wins over a <meta> token, because it is the one the sidecar was started with", async () => {
    const api = await load("meta-tok");
    window.__CHIMERA_SERVER_TOKEN__ = "native-tok";
    await api.getConfig();

    expect(header(calls[0].init ?? {}, "authorization")).toBe("Bearer native-tok");
  });

  it("authenticates an upload too, still without a Content-Type", async () => {
    const api = await load();
    window.__CHIMERA_SERVER_TOKEN__ = "native-tok";
    await api.uploadAttachment(new File(["hello"], "a.txt"));

    expect(calls[0].url).toBe("/api/attachments");
    expect(header(calls[0].init, "authorization")).toBe("Bearer native-tok");
    expect(header(calls[0].init, "content-type")).toBeNull();
  });

  it("authenticates a workspace image fetch without putting the token in the URL", async () => {
    const api = await load();
    window.__CHIMERA_SERVER_TOKEN__ = "native-tok";
    await api.getFsImage(null, "out/chart.png").catch(() => undefined);

    expect(calls[0].url).toBe("/api/fs/image?path=out%2Fchart.png");
    expect(header(calls[0].init, "authorization")).toBe("Bearer native-tok");
  });
});
