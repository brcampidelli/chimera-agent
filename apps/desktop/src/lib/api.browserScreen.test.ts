/**
 * Browser screen pairing on the wire (`chimera/api/browser_screen_api.py`).
 *
 * Minting, listing and revoking carry the desktop's screen credential and nothing else; the exchange
 * carries the code and no screen (a browser tab has none). A screen-only refusal in a tab with no
 * screen announces itself so the browser can offer the code prompt; a refusal without the server's
 * header does not. `fetch` is stubbed so these assert on what would go over the wire.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SCREEN_NEEDED_EVENT } from "@/lib/browser-screen";

let calls: { url: string; init: RequestInit }[];
let respond: (url: string) => Response;

beforeEach(() => {
  calls = [];
  respond = () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as unknown as Response;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return respond(url);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.__CHIMERA_SERVER_TOKEN__;
  delete window.__CHIMERA_SCREEN_TOKEN__;
});

const header = (init: RequestInit, name: string) => new Headers(init.headers).get(name);

function refusedForScreen(): Response {
  return {
    ok: false,
    status: 403,
    headers: new Headers({ "x-chimera-needs-screen": "1" }),
    json: async () => ({ detail: "owner screen credential required" }),
    text: async () => JSON.stringify({ detail: "owner screen credential required" }),
  } as unknown as Response;
}

describe("browser screen pairing requests", () => {
  it("mints a code with the desktop's screen credential beside the bearer", async () => {
    window.__CHIMERA_SERVER_TOKEN__ = "bearer";
    window.__CHIMERA_SCREEN_TOKEN__ = "screen";
    respond = () =>
      ({ ok: true, status: 200, json: async () => ({ code: "ABCD-EFGH", expires_at: 1, seconds: 300 }) }) as unknown as Response;
    const api = await import("@/lib/api");
    await api.mintBrowserScreenCode();

    expect(calls[0].url).toBe("/api/security/browser-screen/code");
    expect(calls[0].init.method).toBe("POST");
    expect(header(calls[0].init, "X-Chimera-Screen")).toBe("screen");
    expect(header(calls[0].init, "Authorization")).toBe("Bearer bearer");
  });

  it("exchanges the typed code with no screen credential, in the body", async () => {
    window.__CHIMERA_SERVER_TOKEN__ = "bearer";
    const api = await import("@/lib/api");
    await api.exchangeBrowserScreenCode("ABCD-EFGH");

    expect(calls[0].url).toBe("/api/security/browser-screen/exchange");
    expect(header(calls[0].init, "X-Chimera-Screen")).toBeNull();
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ code: "ABCD-EFGH" });
  });

  it("revokes one session by its id, encoded, with the screen credential", async () => {
    window.__CHIMERA_SCREEN_TOKEN__ = "screen";
    const api = await import("@/lib/api");
    await api.revokeBrowserScreenSession("a/b");

    expect(calls[0].url).toBe("/api/security/browser-screen/sessions/a%2Fb");
    expect(calls[0].init.method).toBe("DELETE");
    expect(header(calls[0].init, "X-Chimera-Screen")).toBe("screen");
  });
});

describe("a screen-only refusal in a tab with no screen", () => {
  it("announces itself, so the browser can offer the code prompt", async () => {
    respond = () => refusedForScreen();
    const heard = vi.fn();
    window.addEventListener(SCREEN_NEEDED_EVENT, heard);
    const api = await import("@/lib/api");

    await expect(api.getBrowserLink()).rejects.toBeDefined();
    expect(heard).toHaveBeenCalledTimes(1);
    window.removeEventListener(SCREEN_NEEDED_EVENT, heard);
  });

  it("stays quiet for a refusal the server did not mark as one a code could answer", async () => {
    respond = () =>
      ({
        ok: false,
        status: 403,
        headers: new Headers(),
        json: async () => ({ detail: "a paired device cannot pair a browser" }),
        text: async () => "",
      }) as unknown as Response;
    const heard = vi.fn();
    window.addEventListener(SCREEN_NEEDED_EVENT, heard);
    const api = await import("@/lib/api");

    await expect(api.getBrowserLink()).rejects.toBeDefined();
    expect(heard).not.toHaveBeenCalled();
    window.removeEventListener(SCREEN_NEEDED_EVENT, heard);
  });
});
