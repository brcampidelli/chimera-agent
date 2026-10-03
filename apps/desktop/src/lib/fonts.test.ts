import { afterEach, describe, expect, it, vi } from "vitest";

import { anyInstalled, bundledFontLoads, fontInstalled } from "@/lib/fonts";

/**
 * A canvas whose text width depends on the first family named in `font`: an installed family measures
 * differently from the generic, a missing one falls back and measures the same. That is exactly how a
 * real canvas behaves, reduced to the one property the detection reads.
 */
const original = HTMLCanvasElement.prototype.getContext;

function fakeCanvas(installed: readonly string[]): void {
  HTMLCanvasElement.prototype.getContext = function getContext() {
    const ctx = {
      font: "",
      measureText(text: string) {
        const family = /"([^"]+)"/.exec(ctx.font)?.[1];
        const extra = family && installed.includes(family) ? 7 : 0;
        return { width: text.length * 10 + extra };
      },
    };
    return ctx;
  } as unknown as typeof HTMLCanvasElement.prototype.getContext;
}

describe("fontInstalled", () => {
  afterEach(() => {
    HTMLCanvasElement.prototype.getContext = original;
  });

  it("says a font is here when drawing with it changes the width", () => {
    fakeCanvas(["JetBrains Mono"]);
    expect(fontInstalled("JetBrains Mono")).toBe(true);
  });

  it("says a font is missing when every generic draws the same with and without it", () => {
    fakeCanvas([]);
    expect(fontInstalled("JetBrains Mono")).toBe(false);
  });

  it("answers null, not false, when there is no canvas to measure on", () => {
    // The test setup's getContext returns null, as a canvas-less webview would.
    expect(fontInstalled("JetBrains Mono")).toBeNull();
  });

  it("counts a pair of names as here when either one is", () => {
    fakeCanvas(["Cascadia Mono"]);
    expect(anyInstalled("Cascadia Code", "Cascadia Mono")).toBe(true);
  });
});

describe("bundledFontLoads", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(document, "fonts");
  });

  function fontsThat(load: () => Promise<unknown[]>): void {
    Object.defineProperty(document, "fonts", { configurable: true, value: { load } });
  }

  it("is true when the font's file loads", async () => {
    fontsThat(() => Promise.resolve([{}]));
    await expect(bundledFontLoads("OpenDyslexic")).resolves.toBe(true);
  });

  it("is false when the file is missing, whether the load rejects or finds no face", async () => {
    fontsThat(() => Promise.reject(new Error("NetworkError")));
    await expect(bundledFontLoads("OpenDyslexic")).resolves.toBe(false);
    fontsThat(() => Promise.resolve([]));
    await expect(bundledFontLoads("OpenDyslexic")).resolves.toBe(false);
  });

  it("is null where there is no font loading API to ask", async () => {
    await expect(bundledFontLoads("OpenDyslexic")).resolves.toBeNull();
  });
});
