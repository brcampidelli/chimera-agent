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
