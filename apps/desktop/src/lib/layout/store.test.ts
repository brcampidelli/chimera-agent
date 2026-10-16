import { describe, expect, it } from "vitest";

import { applyLayout, defaultLayout, type LayoutAction } from "@/lib/layout/model";
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

  it("migrates a layout saved before tabs and splits without changing its panel arrangement", () => {
    const oldLayout = defaultLayout();
    const stored = JSON.parse(JSON.stringify(oldLayout)) as Record<string, unknown>;
    delete stored.tabGroups;
    delete stored.activeTabs;
    delete stored.splits;

    const migrated = parseLayout(stored);

    // The whole layout, not a few fields: an old layout opens exactly as it did.
    expect(migrated).toEqual(oldLayout);
    expect(migrated.panels).toEqual(oldLayout.panels);
    expect(migrated.regions).toEqual(oldLayout.regions);
    expect(migrated.tabGroups).toEqual([]);
    expect(migrated.activeTabs).toEqual({});
    expect(migrated.splits).toEqual({});
  });

  it("round-trips grouped panels, selected tab, split direction and divider position", () => {
    const actions: LayoutAction[] = [
      { type: "group-tabs", panels: ["activity.tools", "activity.tokens"] },
      { type: "activate-tab", group: "activity.tools", panel: "activity.tools" },
      { type: "set-split", zone: "right", orientation: "horizontal", panel: "activity.jobs" },
      { type: "resize-split", zone: "right", ratio: 0.64 },
    ];
    const arranged = actions.reduce(applyLayout, defaultLayout());
    expect(arranged.splits.right).toEqual({ orientation: "horizontal", ratio: 0.64, second: ["activity.jobs"] });

    // Through the same storage the app uses, not only through JSON.
    const storage = new Map<string, string>();
    saveLayout(arranged, { setItem: (k, v) => void storage.set(k, v) });
    expect(loadLayout({ getItem: (k) => storage.get(k) ?? null })).toEqual(arranged);
  });

  it("reads valid tab groups and split state while rejecting invalid members", () => {
    const stored = JSON.parse(JSON.stringify(defaultLayout())) as Record<string, unknown>;
    stored.tabGroups = [["activity.tools", "activity.tokens", "not-a-panel"], ["sessions", "activity.jobs"]];
    stored.activeTabs = { "activity.tools": "activity.tokens" };
    stored.splits = {
      // `activity.tokens` is named without its group's first member, `sessions` cannot move and
      // `nope` does not exist: none of them may land in the second half.
      right: { orientation: "vertical", ratio: 0.73, second: ["activity.jobs", "activity.tokens", "sessions", "nope"] },
      left: { orientation: "invalid", ratio: 0.4 },
      bottom: { orientation: "horizontal", ratio: 99 },
    };
    const migrated = parseLayout(stored);

    expect(migrated.tabGroups).toEqual([["activity.tools", "activity.tokens"]]);
    expect(migrated.activeTabs["activity.tools"]).toBe("activity.tokens");
    expect(migrated.splits.right).toEqual({ orientation: "vertical", ratio: 0.73, second: ["activity.jobs"] });
    expect(migrated.splits.left).toBeUndefined();
    // A split saved before halves had members reads as an unsplit-looking dock, clamped.
    expect(migrated.splits.bottom).toEqual({ orientation: "horizontal", ratio: 0.8, second: [] });
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
