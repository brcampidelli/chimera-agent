import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import indexHtml from "../../index.html?raw";
import { followStoredAppearance } from "@/lib/theme";
import { applyStoredUiZoom, getUiZoom, setUiZoom, UI_ZOOM_EVENT, UI_ZOOM_KEY, UI_ZOOM_VALUES } from "@/lib/uiZoom";

/**
 * Interface zoom (C8), the module the keyboard shortcuts (C4) will share. Three things have to hold:
 * the value survives a restart, it is on screen before first paint, and a conversation window follows
 * a change made in the main one.
 */
afterEach(() => {
  localStorage.clear();
  document.documentElement.style.zoom = "";
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
});

describe("the zoom value", () => {
  it("is 100 when nothing, or something it does not know, is stored", () => {
    expect(getUiZoom()).toBe(100);
    localStorage.setItem(UI_ZOOM_KEY, "115");
    expect(getUiZoom()).toBe(100);
    localStorage.setItem(UI_ZOOM_KEY, "big");
    expect(getUiZoom()).toBe(100);
  });

  it("persists, paints the root, and tells this window", () => {
    const heard = vi.fn();
    window.addEventListener(UI_ZOOM_EVENT, heard);
    setUiZoom(120);
    window.removeEventListener(UI_ZOOM_EVENT, heard);
    expect(localStorage.getItem(UI_ZOOM_KEY)).toBe("120");
    expect(getUiZoom()).toBe(120);
    expect(document.documentElement.style.zoom).toBe("120%");
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("stamps nothing at 100, so the default page is the page as it was", () => {
    setUiZoom(90);
    setUiZoom(100);
    expect(document.documentElement.style.zoom).toBe("");
  });

  it("refuses a value outside the list instead of storing it", () => {
    setUiZoom(110);
    setUiZoom(115 as unknown as (typeof UI_ZOOM_VALUES)[number]);
    expect(localStorage.getItem(UI_ZOOM_KEY)).toBe("110");
  });

  it("is followed by a conversation window when another window changes it", () => {
    const stop = followStoredAppearance();
    localStorage.setItem(UI_ZOOM_KEY, "80");
    window.dispatchEvent(new StorageEvent("storage", { key: UI_ZOOM_KEY }));
    expect(document.documentElement.style.zoom).toBe("80%");
    stop();
  });

  it("is re-applied from storage at startup", () => {
    localStorage.setItem(UI_ZOOM_KEY, "90");
    applyStoredUiZoom();
    expect(document.documentElement.style.zoom).toBe("90%");
  });
});

describe("the zoom before first paint", () => {
  /** Runs the inline script of index.html as the webview does, before any bundle. */
  function runPrePaintScript() {
    const m = indexHtml.match(/<script>([\s\S]*?)<\/script>/);
    expect(m, "index.html has an inline pre-paint script").not.toBeNull();
    if (typeof window.matchMedia !== "function") {
      vi.stubGlobal("matchMedia", () => ({ matches: false }));
    }
    new Function(m?.[1] ?? "")();
  }

  it("applies a stored zoom — storage holds strings, so a numeric list there never matched", () => {
    localStorage.setItem(UI_ZOOM_KEY, "110");
    runPrePaintScript();
    expect(document.documentElement.style.zoom).toBe("110%");
  });

  it("accepts exactly the values the module accepts", () => {
    const at = indexHtml.indexOf(`pick("${UI_ZOOM_KEY}", [`);
    expect(at).toBeGreaterThan(-1);
    const listed = (indexHtml.slice(at).match(/\[([^\]]*)\]/)?.[1] ?? "")
      .split(",")
      .map((v) => v.trim().replace(/"/g, ""));
    expect(listed).toEqual(UI_ZOOM_VALUES.map(String));
  });

  it("leaves a stored value it does not know unpainted", () => {
    localStorage.setItem(UI_ZOOM_KEY, "500");
    runPrePaintScript();
    expect(document.documentElement.style.zoom).toBe("");
  });
});

describe("the code preview's theme scope in index.css", () => {
  // Vitest stubs CSS imports to "", so the stylesheet is read from disk (see design-system.test.ts).
  const css = readFileSync(join(process.cwd(), "src/index.css"), "utf8");
  const diffView = readFileSync(join(process.cwd(), "src/components/code/DiffView.tsx"), "utf8");

  it("puts each preview half in the SAME rule as its theme's palette, so no palette is written twice", () => {
    expect(css).toMatch(/\.theme-scope\[data-theme-scope='dark'\],\s*:root\s*\{[^}]*--background:/);
    expect(css).toMatch(/\.theme-scope\[data-theme-scope='light'\],\s*:root\[data-theme='light'\]\s*\{\s*--background:/);
  });

  it("re-resolves on the scope every colour DiffView paints with", () => {
    // Tailwind resolves --color-* on :root; without this the scope's palette changes nothing on screen.
    const block = css.match(/\n\s*\.theme-scope\s*\{([^}]*)\}/)?.[1] ?? "";
    const used = new Set(
      [...diffView.matchAll(/\b(?:text|bg)-([a-z0-9-]+?)(?:\/\d+)?(?=[\s"])/g)]
        .map((m) => m[1])
        .filter((name) => !["xs", "sm", "base", "lg", "xl"].includes(name)),
    );
    expect(used.size).toBeGreaterThan(3);
    for (const name of used) {
      expect(block, `--color-${name} re-resolved on .theme-scope`).toContain(`--color-${name}: hsl(var(--${name}))`);
    }
  });
});

describe("the OpenDyslexic face", () => {
  it("points at src/assets/fonts, where the licensed file goes, so adding it is the whole change", () => {
    const css = readFileSync(join(process.cwd(), "src/index.css"), "utf8");
    const url = css.match(/font-family: 'OpenDyslexic';\s*src: url\('([^']+)'\)/)?.[1];
    expect(url).toBe("../assets/fonts/OpenDyslexic-Regular.woff2");
    // Resolved from src/index.css, that is apps/desktop/src/assets/fonts/OpenDyslexic-Regular.woff2.
    expect(join("src", url ?? "")).toBe(join("assets", "fonts", "OpenDyslexic-Regular.woff2"));
  });
});
