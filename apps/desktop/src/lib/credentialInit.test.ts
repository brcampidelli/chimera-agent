import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * P0-19: the shell's initialization script runs before EVERY document the webview creates — any
 * page the window navigates to, and on Windows every subframe (the dev-server preview, the Design
 * preview). This evaluates the script exactly as Rust composes it — `init_script` in main.rs, with
 * the app at `http://127.0.0.1:8765` — which a Rust test keeps checked in as
 * `src-tauri/src/credential_init.fixture.js` and fails on if the two diverge. It runs against
 * stand-in windows and checks that only the app's own top-level document gets the credentials.
 */
const COMPOSED = readFileSync(join(__dirname, "..", "..", "src-tauri", "src", "credential_init.fixture.js"), "utf8");

interface FakeWindow {
  top?: FakeWindow;
  location: { protocol: string; hostname: string; pathname: string };
  __CHIMERA_SERVER_TOKEN__?: string;
  __CHIMERA_SCREEN_TOKEN__?: string;
}

function run(win: FakeWindow): FakeWindow {
  // The composed script is a statement that reads the global `window`; here that name is bound to
  // the stand-in, and nothing else is supplied — the arguments are the ones Rust wrote.
  new Function("window", COMPOSED)(win);
  return win;
}

function topLevel(protocol: string, hostname: string, pathname = "/"): FakeWindow {
  const win: FakeWindow = { location: { protocol, hostname, pathname } };
  win.top = win;
  return win;
}

describe("the credential initialization script", () => {
  it("hands both credentials to the app's own top-level document", () => {
    const win = run(topLevel("http:", "127.0.0.1"));
    expect(win.__CHIMERA_SERVER_TOKEN__).toBe("server-secret");
    expect(win.__CHIMERA_SCREEN_TOKEN__).toBe("screen-secret");
  });

  it("hands nothing to a site the window was taken to", () => {
    for (const [protocol, hostname] of [
      ["https:", "example.com"],
      ["http:", "localhost"],
      ["https:", "127.0.0.1"],
      ["about:", ""],
      ["data:", ""],
    ]) {
      const win = run(topLevel(protocol, hostname));
      expect(win.__CHIMERA_SERVER_TOKEN__, `${protocol}//${hostname}`).toBeUndefined();
      expect(win.__CHIMERA_SCREEN_TOKEN__, `${protocol}//${hostname}`).toBeUndefined();
    }
  });

  it("hands nothing to a subframe, even one on the app's own host", () => {
    const app = topLevel("http:", "127.0.0.1");
    // The dev-server preview (another port, same host) and a sandboxed preview (an opaque origin).
    for (const location of [
      { protocol: "http:", hostname: "127.0.0.1", pathname: "/" },
      { protocol: "about:", hostname: "", pathname: "srcdoc" },
    ]) {
      const frame = run({ top: app, location });
      expect(frame.__CHIMERA_SERVER_TOKEN__).toBeUndefined();
      expect(frame.__CHIMERA_SCREEN_TOKEN__).toBeUndefined();
    }
    expect(app.__CHIMERA_SERVER_TOKEN__).toBeUndefined();
  });

  it("hands both credentials to the app's entry page and its routes", () => {
    for (const pathname of ["/", "/index.html", "/code/abc", "/code/abc_1-2/"]) {
      const win = run(topLevel("http:", "127.0.0.1", pathname));
      expect(win.__CHIMERA_SERVER_TOKEN__, pathname).toBe("server-secret");
      expect(win.__CHIMERA_SCREEN_TOKEN__, pathname).toBe("screen-secret");
    }
  });

  it("hands nothing to another page of the app's own origin", () => {
    // `location.pathname` as the browser gives it: query and hash are elsewhere, `/./` is resolved,
    // case and percent-escapes are kept. The backend still serves the share-link page for each of
    // these spellings (Windows ignores case; it decodes `%67` and `%2e`).
    for (const pathname of [
      "/guest.html",
      "/guest.html/",
      "/GUEST.html",
      "/%67uest.html",
      "/guest%2ehtml",
      "/guest",
      "/Guest/x",
      "/api/info",
      "/v1/models",
      "/V1/chat",
      "/assets/x.html",
      "/sw.js",
    ]) {
      const win = run(topLevel("http:", "127.0.0.1", pathname));
      expect(win.__CHIMERA_SERVER_TOKEN__, pathname).toBeUndefined();
      expect(win.__CHIMERA_SCREEN_TOKEN__, pathname).toBeUndefined();
    }
  });
});
