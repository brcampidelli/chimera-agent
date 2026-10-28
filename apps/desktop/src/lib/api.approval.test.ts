/**
 * The approval card's yes carries the screen credential, and nothing else does.
 *
 * The backend approves a question it asked without the question's code only when the request came
 * from the owner's screen (`chimera/api/screen_credential.py`): the bearer token alone is not enough,
 * because a service keeps one on disk. So the one call that needs the credential must send it — or
 * every click on the card turns into "needs a code" — and no other call should carry it around.
 * `fetch` is stubbed rather than the module, so these assert on what would go over the wire.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let calls: { url: string; init: RequestInit }[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return { ok: true, status: 200, json: async () => ({ ok: true }) } as Response;
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete window.__CHIMERA_SERVER_TOKEN__;
  delete window.__CHIMERA_SCREEN_TOKEN__;
});

const header = (init: RequestInit, name: string) => new Headers(init.headers).get(name);

describe("the approval card's answer", () => {
  it("sends the shell's screen credential beside the bearer token", async () => {
    window.__CHIMERA_SERVER_TOKEN__ = "bearer";
    window.__CHIMERA_SCREEN_TOKEN__ = "screen";
    const api = await import("@/lib/api");
    await api.answerApproval("abc123", true);

    expect(calls[0].url).toBe("/api/approvals/abc123");
    expect(header(calls[0].init, "X-Chimera-Screen")).toBe("screen");
    expect(header(calls[0].init, "Authorization")).toBe("Bearer bearer");
    expect(header(calls[0].init, "Content-Type")).toBe("application/json");
  });

  it("sends the screen credential with a typed answer, which a design approval needs", async () => {
    window.__CHIMERA_SERVER_TOKEN__ = "bearer";
    window.__CHIMERA_SCREEN_TOKEN__ = "screen";
    const api = await import("@/lib/api");
    await api.answerUserQuestion("0123456789ab", "approved");

    expect(calls[0].url).toBe("/api/questions/0123456789ab");
    expect(header(calls[0].init, "X-Chimera-Screen")).toBe("screen");
  });

  it("sends the screen credential with a conversation permission change", async () => {
    window.__CHIMERA_SERVER_TOKEN__ = "bearer";
    window.__CHIMERA_SCREEN_TOKEN__ = "screen";
    const api = await import("@/lib/api");
    await api.patchCodeBypass("session-1", true);

    expect(calls[0].url).toBe("/api/code/sessions/session-1/permissions");
    expect(calls[0].init.method).toBe("PATCH");
    expect(header(calls[0].init, "X-Chimera-Screen")).toBe("screen");
  });

  it("sends no screen header when the window has none", async () => {
    const api = await import("@/lib/api");
    await api.answerApproval("abc123", false);
    expect(header(calls[0].init, "X-Chimera-Screen")).toBeNull();
  });

  it("is the only request that carries it", async () => {
    window.__CHIMERA_SCREEN_TOKEN__ = "screen";
    const api = await import("@/lib/api");
    await api.getApprovals();
    expect(header(calls[0].init, "X-Chimera-Screen")).toBeNull();
  });
});
