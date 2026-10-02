import { describe, expect, it } from "vitest";

import { applyLayout, defaultLayout } from "@/lib/layout/model";
import { STORAGE_KEY, loadLayout, parseLayout, saveLayout } from "@/lib/layout/store";

/**
 * A stored layout can never break the screen. Everything this code does not recognise becomes the
 * default, a panel a newer build added takes its default place, and a storage that throws costs the
 * layout and nothing else.
 */
describe("reading a stored layout", () => {
  it("reads back what it wrote", () => {
    const layout = [
      { type: "set-region", region: "left", visible: false } as const,
      { type: "move", panel: "activity.tools", zone: "bottom", index: 0 } as const,
      { type: "resize", region: "right", size: 400 } as const,
      { type: "toggle-focus" } as const,
    ].reduce(applyLayout, defaultLayout());

    expect(parseLayout(JSON.parse(JSON.stringify(layout)))).toEqual(layout);
  });

  it.each([
    ["nothing", undefined],
    ["a string", "left"],
    ["an array", []],
    ["another version", { ...defaultLayout(), version: 2 }],
    ["no version", { regions: {} }],
  ])("gives the default layout for %s", (_name, raw) => {
    expect(parseLayout(raw)).toEqual(defaultLayout());
  });

  it("drops what it does not know and fills what is missing", () => {
    const stored = JSON.parse(JSON.stringify(defaultLayout()));
    delete stored.panels["activity.jobs"]; // a panel a newer build added
    stored.panels["activity.removed"] = { zone: "right", order: 9, mode: "open" }; // one that went away
    stored.panels["activity.tools"] = { zone: "the-moon", order: 1, mode: "open" }; // not a zone
    stored.cards.tools = "exploded";

    const layout = parseLayout(stored);

    expect(layout.panels["activity.jobs"]).toEqual(defaultLayout().panels["activity.jobs"]);
    expect(Object.keys(layout.panels)).not.toContain("activity.removed");
    expect(layout.panels["activity.tools"]).toEqual(defaultLayout().panels["activity.tools"]);
    expect(layout.cards.tools).toBe("open");
  });

  it("never reads a panel back closed when it may not close, nor away from home when it may not move", () => {
    const stored = JSON.parse(JSON.stringify(defaultLayout()));
    stored.panels["composer.config"] = { zone: "left", order: 0, mode: "closed" };

    expect(parseLayout(stored).panels["composer.config"]).toMatchObject({ zone: "composer", mode: "open" });
  });

  it("clamps a stored size that is out of range", () => {
    const stored = JSON.parse(JSON.stringify(defaultLayout()));
    stored.regions.left.size = 9999;
    stored.regions.right.size = "wide";

    const layout = parseLayout(stored);
    expect(layout.regions.left.size).toBe(420);
    expect(layout.regions.right.size).toBe(defaultLayout().regions.right.size);
  });
});

describe("the storage", () => {
  it("gives the default when the stored text is not JSON, and when reading throws", () => {
    expect(loadLayout({ getItem: () => "{not json" })).toEqual(defaultLayout());
    expect(
      loadLayout({
        getItem: () => {
          throw new Error("storage is locked");
        },
      }),
    ).toEqual(defaultLayout());
  });

  it("swallows a storage that refuses to write, and writes under the screen's key when it can", () => {
    expect(() =>
      saveLayout(defaultLayout(), {
        setItem: () => {
          throw new Error("quota");
        },
      }),
    ).not.toThrow();

    const written: Record<string, string> = {};
    saveLayout(defaultLayout(), { setItem: (k, v) => void (written[k] = v) });
    expect(Object.keys(written)).toEqual([STORAGE_KEY]);
    expect(parseLayout(JSON.parse(written[STORAGE_KEY]))).toEqual(defaultLayout());
  });
});
