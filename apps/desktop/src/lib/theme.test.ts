import { afterEach, describe, expect, it, vi } from "vitest";

// The page shell, read through Vite rather than node:fs — Vitest shares the app's transform
// pipeline, so `?raw` works here and costs no @types/node.
import indexHtml from "../../index.html?raw";

import {
  applyCodeFont,
  applyMotion,
  applyTextSize,
  applyTheme,
  applyUiFont,
  CODE_FONT_KEY,
  followStoredAppearance,
  CODE_FONTS,
  MOTION_KEY,
  readCodeFont,
  readTextSize,
  readUiFont,
  TEXT_SIZE_KEY,
  TEXT_SIZES,
  UI_FONT_KEY,
  UI_FONTS,
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

describe("text size and fonts", () => {
  it("stamps an explicit choice and removes the attribute for the default", () => {
    const root = document.documentElement;
    applyTextSize("large");
    applyUiFont("dyslexic");
    applyCodeFont("jetbrains");
    expect(root.dataset.textSize).toBe("large");
    expect(root.dataset.font).toBe("dyslexic");
    expect(root.dataset.fontCode).toBe("jetbrains");
    // The defaults stamp nothing, so the CSS that reacts to these matches nothing: today's page.
    applyTextSize("medium");
    applyUiFont("system");
    applyCodeFont("default");
    expect(root.dataset.textSize).toBeUndefined();
    expect(root.dataset.font).toBeUndefined();
    expect(root.dataset.fontCode).toBeUndefined();
  });

  it("round-trips each preference through storage", () => {
    applyTextSize("small");
    applyUiFont("serif");
    applyCodeFont("cascadia");
    expect([readTextSize(), readUiFont(), readCodeFont()]).toEqual(["small", "serif", "cascadia"]);
  });

  it("reads the default when nothing, or something unknown, is stored", () => {
    expect([readTextSize(), readUiFont(), readCodeFont()]).toEqual(["medium", "system", "default"]);
    localStorage.setItem(TEXT_SIZE_KEY, "gigantic");
    localStorage.setItem(UI_FONT_KEY, "comic");
    localStorage.setItem(CODE_FONT_KEY, "wingdings");
    expect([readTextSize(), readUiFont(), readCodeFont()]).toEqual(["medium", "system", "default"]);
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
    expect(html).toContain(`"${TEXT_SIZE_KEY}"`);
    expect(html).toContain(`"${UI_FONT_KEY}"`);
    expect(html).toContain(`"${CODE_FONT_KEY}"`);
  });

  it("accepts exactly the values this module accepts, for text size and both fonts", () => {
    // The list in the script is what decides whether a stored value is honoured before paint; a value
    // added here and not there would be applied by React a frame late, which is the flash again.
    const listed = (key: string) => {
      const at = html.indexOf(`pick("${key}", [`);
      const m = at < 0 ? null : html.slice(at).match(/\[([^\]]*)\]/);
      return (m?.[1] ?? "").split(",").map((v) => v.trim().replace(/"/g, "")).filter(Boolean).sort();
    };
    expect(listed(TEXT_SIZE_KEY)).toEqual([...TEXT_SIZES].sort());
    expect(listed(UI_FONT_KEY)).toEqual([...UI_FONTS].sort());
    expect(listed(CODE_FONT_KEY)).toEqual([...CODE_FONTS].sort());
  });

  it("sets both attributes before the app bundle loads", () => {
    expect(html).toContain("dataset.theme");
    expect(html).toContain("dataset.motion");
    expect(html).toContain("dataset.textSize");
    expect(html).toContain("dataset.font ");
    expect(html).toContain("dataset.fontCode");
    // Ordering is the whole point: after the module script, the flash is back.
    expect(html.indexOf("chimera.theme")).toBeLessThan(html.indexOf("/src/main.tsx"));
  });

  it("agrees with resolveTheme that dark is the fallback", () => {
    expect(html).toMatch(/prefers-color-scheme: light.*\?\s*"light"\s*:\s*"dark"/s);
  });
});

describe("a secondary window following the main one", () => {
  /** What the main window does: write storage. The event is what the browser sends every OTHER window. */
  function changedElsewhere(key: string | null, value?: string): void {
    if (key !== null && value !== undefined) localStorage.setItem(key, value);
    window.dispatchEvent(new StorageEvent("storage", { key }));
  }

  it("repaints text size, fonts, motion and theme when the main window changes them", () => {
    const stop = followStoredAppearance();
    changedElsewhere(TEXT_SIZE_KEY, "large");
    changedElsewhere(UI_FONT_KEY, "serif");
    changedElsewhere(CODE_FONT_KEY, "jetbrains");
    changedElsewhere(MOTION_KEY, "reduced");
    changedElsewhere(THEME_KEY, "light");
    const root = document.documentElement.dataset;
    expect(root.textSize).toBe("large");
    expect(root.font).toBe("serif");
    expect(root.fontCode).toBe("jetbrains");
    expect(root.motion).toBe("reduced");
    expect(root.theme).toBe("light");

    // Back to a default stamps nothing again, so the window is today's page.
    changedElsewhere(TEXT_SIZE_KEY, "medium");
    expect(root.textSize).toBeUndefined();
    stop();
  });

  it("goes back to every default when storage is cleared, and stops after unsubscribing", () => {
    const stop = followStoredAppearance();
    changedElsewhere(TEXT_SIZE_KEY, "small");
    expect(document.documentElement.dataset.textSize).toBe("small");
    localStorage.clear();
    changedElsewhere(null);
    expect(document.documentElement.dataset.textSize).toBeUndefined();

    stop();
    changedElsewhere(TEXT_SIZE_KEY, "large");
    expect(document.documentElement.dataset.textSize).toBeUndefined();
  });

  it("ignores keys that are not appearance and never writes back what it reads", () => {
    const stop = followStoredAppearance();
    localStorage.setItem(TEXT_SIZE_KEY, "large");
    changedElsewhere("chimera.something-else", "x");
    expect(document.documentElement.dataset.textSize).toBeUndefined();
    changedElsewhere(TEXT_SIZE_KEY, "large");
    // A reader only: the theme it painted was not written to storage on its behalf.
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    stop();
  });
});
