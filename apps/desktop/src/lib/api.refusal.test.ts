/**
 * What a screen is told when the server refuses.
 *
 * Every refusal in this app arrived as its status line — "400 Bad Request" — and the sentence the
 * backend wrote was dropped on the floor by the one helper every call goes through. Which made a
 * whole class of server-side care pointless: a route that answers `workspace not found: C:\...`
 * with the path in it, so a person can see WHICH folder was missing, was answering into a wall.
 *
 * Stubbed at `fetch` rather than at the module, for the reason `api.upload.test.ts` gives: the
 * component tests mock `@/lib/api` wholesale, so the code with the bug in it never runs there.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Api = typeof import("@/lib/api");

async function load(): Promise<Api> {
  vi.resetModules();
  return import("@/lib/api");
}

function refused(status: number, body: unknown, statusText = "Bad Request"): Response {
  return {
    ok: false,
    status,
    statusText,
    json: async () => {
      if (body === undefined) throw new SyntaxError("Unexpected end of JSON input");
      return body;
    },
  } as Response;
}

function serve(response: Response) {
  vi.stubGlobal("fetch", vi.fn(async () => response));
}

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => vi.resetModules());

describe("a refused request", () => {
  it("tells the screen the server's own sentence, not the status line", async () => {
    serve(refused(400, { detail: "workspace not found: C:\\Users\\brcam\\nao-existe" }));
    const api = await load();

    // The whole reason the backend names the path: so this is the string a person reads.
    await expect(api.getConfig()).rejects.toThrow(/nao-existe/);
  });

  it("falls back to the status line when the body is not JSON", async () => {
    // A proxy page, an empty body, a connection cut mid-response — the status line is all there
    // is, and it has to be better than an exception thrown while reporting one.
    serve(refused(502, undefined, "Bad Gateway"));
    const api = await load();

    await expect(api.getConfig()).rejects.toThrow("502 Bad Gateway");
  });

  it("ignores a detail that is a list of validation objects", async () => {
    // FastAPI writes `detail` two ways: a string for a raised HTTPException, and a list of
    // objects for a schema rejection. Only the first was written for a human to read; rendering
    // the second would put `[object Object]` on the screen.
    serve(refused(422, { detail: [{ loc: ["body", "task"], msg: "field required" }] }));
    const api = await load();

    await expect(api.getConfig()).rejects.toThrow("422 Bad Request");
  });

  it("does not let an enormous body become the error message", async () => {
    serve(refused(400, { detail: "x".repeat(5000) }));
    const api = await load();

    await expect(api.getConfig()).rejects.toThrow(/^x{400}$/);
  });
});
