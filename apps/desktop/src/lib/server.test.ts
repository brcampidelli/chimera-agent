import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LOCAL,
  active,
  apiUrl,
  handshake,
  isLoopback,
  normaliseBase,
  rejectReason,
  sameMinor,
  saveServers,
  setActive,
  token,
} from "@/lib/server";

/**
 * The two things this file has to get right are opposites.
 *
 * The local path must be byte-identical to what shipped — a relative URL and the token from the
 * page — because every one of the other 356 tests exercises it and any drift there is a regression
 * for every user who never touches a remote.
 *
 * The remote path must REFUSE more than it accepts. Both refusals protect against something the
 * user cannot see: a token travelling in the clear, and an agent left open to whoever finds the URL.
 */
describe("the server this window talks to", () => {
  beforeEach(() => {
    localStorage.clear();
    document.head.innerHTML = "";
  });
  afterEach(() => localStorage.clear());

  describe("local — unchanged, and that is the point", () => {
    it("builds a relative url, exactly as it shipped", () => {
      expect(apiUrl("/api/sessions")).toBe("/api/sessions");
    });

    it("takes the token from the page, which only a loopback client is given", () => {
      document.head.innerHTML = '<meta name="chimera-token" content="segredo">';
      expect(token()).toBe("segredo");
    });

    it("sends nothing when the page has no token — the localhost default", () => {
      expect(token()).toBe("");
    });

    it("falls back to local when the stored active id no longer exists", () => {
      // A server deleted while it was the active one must not leave the app pointing at nothing:
      // every request would fail and the screen that fixes it is behind those requests.
      setActive("apagado");
      expect(active()).toEqual(LOCAL);
      expect(apiUrl("/api/x")).toBe("/api/x");
    });

    it("survives a corrupt store instead of taking the window down", () => {
      localStorage.setItem("chimera.servers", "{ isto não é json");
      expect(active()).toEqual(LOCAL);
    });
  });

  describe("remote", () => {
    const remoto = { id: "r1", name: "VPS", baseUrl: "https://chimera.exemplo.com", token: "tk" };

    it("prefixes every path with the server's origin", () => {
      saveServers([remoto]);
      setActive("r1");
      expect(apiUrl("/api/sessions")).toBe("https://chimera.exemplo.com/api/sessions");
    });

    it("carries the server's own token, never the page's", () => {
      // A remotely-served page is never given a token, by the backend's own design. If this read
      // the meta tag it would send the LOCAL server's secret to a remote host.
      document.head.innerHTML = '<meta name="chimera-token" content="segredo-local">';
      saveServers([remoto]);
      setActive("r1");
      expect(token()).toBe("tk");
    });

    it("keeps only the origin, so a pasted url with a path does not double it", () => {
      expect(normaliseBase("https://x.exemplo.com/app/?a=1")).toBe("https://x.exemplo.com");
      expect(normaliseBase("https://x.exemplo.com:8765/")).toBe("https://x.exemplo.com:8765");
    });
  });

  describe("the two refusals", () => {
    it("refuses plain http off the loopback — the token is in a header on every request", () => {
      expect(rejectReason("http://chimera.exemplo.com", "tk")).toBe("needsHttps");
    });

    it("refuses a remote with no token — an open agent is worse than no connection", () => {
      expect(rejectReason("https://chimera.exemplo.com", "  ")).toBe("needsToken");
    });

    it("accepts a remote that satisfies both", () => {
      expect(rejectReason("https://chimera.exemplo.com", "tk")).toBeNull();
    });

    it("exempts loopback from both, because that is the local default", () => {
      expect(rejectReason("http://127.0.0.1:8765", "")).toBeNull();
      expect(rejectReason("http://localhost:8765", "")).toBeNull();
      expect(isLoopback("127.0.0.1")).toBe(true);
      expect(isLoopback("chimera.exemplo.com")).toBe(false);
    });

    it("refuses what is not a url, and what is not http", () => {
      expect(rejectReason("nada disso", "tk")).toBe("notUrl");
      expect(rejectReason("file:///etc/passwd", "tk")).toBe("notHttp");
      // `javascript:` in a field the app later navigates or fetches is the classic way a settings
      // screen becomes an execution surface.
      expect(rejectReason("javascript:alert(1)", "tk")).toBe("notHttp");
    });
  });
});

/**
 * Asking a server who it is, before anything depends on the answer.
 *
 * Each failure here is one the user can act on, and they are told apart because the actions are
 * different: allow an origin, fix a token, check the address. The one that cannot be told apart is
 * named honestly rather than guessed at.
 */
describe("the handshake", () => {
  const REMOTO = "https://chimera.exemplo.com";

  beforeEach(() => {
    localStorage.clear();
    document.head.innerHTML = "";
  });
  afterEach(() => vi.unstubAllGlobals());

  const respostas = (map: Record<string, unknown>) => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const hit = Object.entries(map).find(([k]) => url.includes(k));
        if (!hit) return Promise.reject(new TypeError("Failed to fetch"));
        const v = hit[1];
        if (v instanceof Error) return Promise.reject(v);
        return Promise.resolve(v as Response);
      }),
    );
  };

  const ok = (body: unknown) =>
    ({ ok: true, status: 200, json: () => Promise.resolve(body) }) as unknown as Response;

  it("reports the versions on both sides", async () => {
    respostas({ [REMOTO]: ok({ version: "0.43.0" }), "/api/version": ok({ version: "0.43.0" }) });
    const r = await handshake(REMOTO, "tk");
    expect(r).toEqual({ ok: true, version: "0.43.0", appVersion: "0.43.0", sameVersion: true });
  });

  it("flags a server whose minor differs from this app's", async () => {
    respostas({ [REMOTO]: ok({ version: "0.38.0" }), "/api/version": ok({ version: "0.43.0" }) });
    const r = await handshake(REMOTO, "tk");
    expect(r).toMatchObject({ ok: true, version: "0.38.0", appVersion: "0.43.0", sameVersion: false });
  });

  it("does not call a mismatch it cannot see", async () => {
    // The local backend unreachable is possible and is not this connection's problem. Reporting
    // "mismatch" there would blame the remote for something never compared.
    respostas({ [REMOTO]: ok({ version: "0.38.0" }) });
    const r = await handshake(REMOTO, "tk");
    expect(r).toMatchObject({ ok: true, version: "0.38.0", appVersion: "", sameVersion: true });
  });

  it("separates a refused token from an unreachable server", async () => {
    respostas({ [REMOTO]: { ok: false, status: 401 } as Response });
    expect(await handshake(REMOTO, "errado")).toEqual({ ok: false, reason: "unauthorized" });

    respostas({});
    expect(await handshake(REMOTO, "tk")).toEqual({ ok: false, reason: "unreachable" });
  });

  it("refuses a 200 that is not a Chimera", async () => {
    // A proxy error page, a login wall, another product. Treating it as a success would connect
    // the app to something that cannot answer a single other call.
    respostas({ [REMOTO]: { ok: true, status: 200, json: () => Promise.reject(new Error("html")) } as unknown as Response });
    expect(await handshake(REMOTO, "tk")).toMatchObject({ ok: false, reason: "notChimera" });

    respostas({ [REMOTO]: ok({ hello: "world" }) });
    expect(await handshake(REMOTO, "tk")).toMatchObject({ ok: false, reason: "notChimera" });
  });

  it("compares major.minor, which is where this project's api moves", () => {
    expect(sameMinor("0.43.0", "0.43.9")).toBe(true);
    expect(sameMinor("0.43.0", "0.44.0")).toBe(false);
    expect(sameMinor("1.0.0", "0.43.0")).toBe(false);
  });
});
