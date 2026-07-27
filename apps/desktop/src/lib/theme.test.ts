import { afterEach, describe, expect, it, vi } from "vitest";

// The page shell, read through Vite rather than node:fs — Vitest shares the app's transform
// pipeline, so `?raw` works here and costs no @types/node.
import indexHtml from "../../index.html?raw";

import {
  applyMotion,
  applyTheme,
  MOTION_KEY,
  readMotion,
  readTheme,
  resolveReducedMotion,
  resolveTheme,
  THEME_KEY,
} from "@/lib/theme";

/** Force `matchMedia` to answer `matches` for a given query and false for everything else. */
function mockMedia(matching: string): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query === matching,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("resolveTheme", () => {
  it("returns an explicit preference without consulting the OS", () => {
    mockMedia("(prefers-color-scheme: light)");
    expect(resolveTheme("dark")).toBe("dark"); // explicit beats the OS saying light
    expect(resolveTheme("light")).toBe("light");
  });

  it("follows the OS when the preference is system", () => {
    mockMedia("(prefers-color-scheme: light)");
    expect(resolveTheme("system")).toBe("light");
  });

  it("falls back to dark when the OS expresses no light preference", () => {
    mockMedia("(nothing matches this)");
    expect(resolveTheme("system")).toBe("dark");
  });

  it("survives an environment with no matchMedia at all", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(resolveTheme("system")).toBe("dark");
  });
});

describe("applyTheme", () => {
  it("writes the resolved theme to the document and persists the preference", () => {
    mockMedia("(prefers-color-scheme: light)");
    expect(applyTheme("system")).toBe("light");
    // The ATTRIBUTE carries the resolved value (CSS needs something concrete)...
    expect(document.documentElement.dataset.theme).toBe("light");
    // ...while STORAGE keeps the preference, so "follow my OS" survives a restart.
    expect(localStorage.getItem(THEME_KEY)).toBe("system");
  });

  it("round-trips through readTheme", () => {
    applyTheme("dark");
    expect(readTheme()).toBe("dark");
  });

  it("ignores a stored value that is not a known preference", () => {
    localStorage.setItem(THEME_KEY, "chartreuse");
    expect(readTheme()).toBe("system");
  });
});

describe("motion", () => {
  it("lets an explicit preference override the OS in both directions", () => {
    mockMedia("(prefers-reduced-motion: reduce)");
    expect(resolveReducedMotion("full")).toBe(false); // OS says reduce, user said full
    vi.unstubAllGlobals();
    mockMedia("(nothing)");
    expect(resolveReducedMotion("reduced")).toBe(true); // OS is quiet, user asked for calm
  });

  it("defers to the OS flag when the preference is system", () => {
    mockMedia("(prefers-reduced-motion: reduce)");
    expect(resolveReducedMotion("system")).toBe(true);
  });

  it("removes the attribute for system so the media query stays in charge", () => {
    applyMotion("reduced");
    expect(document.documentElement.dataset.motion).toBe("reduced");
    applyMotion("system");
    // Not `data-motion="system"` — the reduced-motion CSS keys off the attribute being ABSENT.
    expect(document.documentElement.dataset.motion).toBeUndefined();
    expect(readMotion()).toBe("system");
  });
});

describe("the inline script in index.html", () => {
  // The pre-paint script duplicates this module's resolution logic on purpose (an external file
  // would be a round-trip in front of the flash it prevents). These assertions are what stop the
  // copy from silently drifting: if someone renames a key here, this test fails there.
  const html = indexHtml;

  it("reads the same storage keys this module writes", () => {
    expect(html).toContain(`"${THEME_KEY}"`);
    expect(html).toContain(`"${MOTION_KEY}"`);
  });

  it("sets both attributes before the app bundle loads", () => {
    expect(html).toContain("dataset.theme");
    expect(html).toContain("dataset.motion");
    // Ordering is the whole point: after the module script, the flash is back.
    expect(html.indexOf("chimera.theme")).toBeLessThan(html.indexOf("/src/main.tsx"));
  });

  it("agrees with resolveTheme that dark is the fallback", () => {
    expect(html).toMatch(/prefers-color-scheme: light.*\?\s*"light"\s*:\s*"dark"/s);
  });
});
