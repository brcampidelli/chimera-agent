import { describe, expect, it } from "vitest";

import { PANEL_IDS, applyLayout, defaultLayout, type LayoutAction, type PanelId } from "@/lib/layout/model";
import { STORAGE_KEY, foldRetiredPanels, loadLayout, parseLayout, saveLayout } from "@/lib/layout/store";

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
    // An arrangement someone made (tools in the bottom dock), with no groups: a layout of that time.
    // Not the untouched default, which since ESTUDO-03 is moved to the new default on purpose (below).
    const oldLayout = applyLayout(
      { ...defaultLayout(), tabGroups: [], activeTabs: {} },
      { type: "move", panel: "activity.tools", zone: "bottom", index: 0 },
    );
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

    expect(Object.keys(layout.panels)).not.toContain("activity.removed");
    // What was missing or unreadable goes open, home, after everything the stored value placed there,
    // in the order the panels were added (tools before jobs), and no two panels share a place.
    const placed = PANEL_IDS.filter((id) => id !== "activity.jobs" && id !== "activity.tools" && layout.panels[id].zone === "right");
    const last = Math.max(...placed.map((id) => layout.panels[id].order));
    expect(layout.panels["activity.tools"]).toEqual({ zone: "right", order: last + 1, mode: "open" });
    expect(layout.panels["activity.jobs"]).toEqual({ zone: "right", order: last + 2, mode: "open" });
    expect(layout.cards.tools).toBe("open");
  });

  it("moves a stored layout nobody rearranged to the new default, and keeps one somebody did", () => {
    // Written out as a 0.68.1 build stored its untouched default: every dock panel open on the right in
    // the order the panels were added, the composer's pickers minimised, no groups, no split. It names
    // "conversation.tasks", folded into the background panel since: the stored value is what a 0.68.1
    // build wrote, and a retired card in it must not make it read as an arrangement.
    const order: string[] = [
      "activity.tools", "activity.tokens", "activity.memory", "activity.fusion", "activity.jobs", "activity.machine",
      "conversation.environment", "conversation.context", "conversation.connectors", "conversation.changes", "conversation.tasks",
    ];
    const panels: Record<string, unknown> = {
      sessions: { zone: "left", order: 0, mode: "open" },
      viewer: { zone: "center", order: 0, mode: "open" },
      "composer.config": { zone: "composer", order: 0, mode: "minimized" },
    };
    order.forEach((id, i) => (panels[id] = { zone: "right", order: i, mode: "open" }));
    const untouched = {
      ...JSON.parse(JSON.stringify(defaultLayout())),
      panels,
      tabGroups: [],
      activeTabs: {},
      splits: {},
      regions: { ...defaultLayout().regions, right: { visible: true, size: 360 } },
    };

    const moved = parseLayout(untouched);
    expect(moved.panels).toEqual(defaultLayout().panels);
    expect(moved.tabGroups).toEqual(defaultLayout().tabGroups);
    // The width is the person's either way.
    expect(moved.regions.right.size).toBe(360);

    // A build from before the conversation's cards stored only the first six, in the same places: also
    // untouched, and moved the same way.
    const preCards = { ...untouched, panels: { ...panels } };
    for (const id of order.slice(6)) delete (preCards.panels as Record<string, unknown>)[id];
    expect(parseLayout(preCards).panels).toEqual(defaultLayout().panels);

    // One panel minimised by hand is an arrangement: read back exactly as stored.
    const touched = { ...untouched, panels: { ...panels, "activity.memory": { zone: "right", order: 2, mode: "minimized" } } };
    const kept = parseLayout(touched);
    for (const id of order.filter((x): x is PanelId => (PANEL_IDS as string[]).includes(x))) {
      expect(kept.panels[id]).toEqual((touched.panels as Record<string, unknown>)[id]);
    }
    expect(kept.tabGroups).toEqual([]);
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

describe("the batch board's place (2026-10-17)", () => {
  /** A layout as a build before the batch board stored it: the same value, without its entry. */
  function storedBefore(layout: ReturnType<typeof defaultLayout>) {
    const raw = JSON.parse(JSON.stringify(layout));
    delete raw.panels["agents.batch"];
    return raw;
  }

  it("reads a layout stored before it existed exactly as it was, with the board home and open", () => {
    const arranged = [
      { type: "move", panel: "activity.tools", zone: "bottom", index: 0 } as const,
      { type: "set-mode", panel: "activity.machine", mode: "closed" } as const,
      { type: "set-mode", panel: "viewer", mode: "minimized" } as const,
    ].reduce(applyLayout, defaultLayout());

    const read = parseLayout(storedBefore(arranged));

    for (const id of PANEL_IDS.filter((id) => id !== "agents.batch")) expect(read.panels[id]).toEqual(arranged.panels[id]);
    expect(read.panels["agents.batch"]).toEqual({ zone: "center", order: 1, mode: "open" });
    expect(read.tabGroups).toEqual(arranged.tabGroups);
    // An untouched default stored before it existed still reads as today's default.
    expect(parseLayout(storedBefore(defaultLayout()))).toEqual(defaultLayout());
  });

  it("keeps where it was put and whether it was minimised or closed", () => {
    for (const step of [
      { type: "move", panel: "agents.batch", zone: "right", index: 2 },
      { type: "set-mode", panel: "agents.batch", mode: "minimized" },
      { type: "set-mode", panel: "agents.batch", mode: "closed" },
    ] satisfies LayoutAction[]) {
      const layout = applyLayout(defaultLayout(), step);
      expect(parseLayout(JSON.parse(JSON.stringify(layout))).panels["agents.batch"]).toEqual(layout.panels["agents.batch"]);
    }
  });
});

/**
 * 0.69 drew the background jobs twice: "Conversation tasks" at the top of the right panel and
 * "Background jobs" at the bottom, both reading the same list. The first was folded into the second,
 * and a stored layout that names it must still open the way the person left it.
 *
 * Written out as the JSON a 0.69 build stored, not derived from today's model: today's model has no
 * "conversation.tasks" to derive it from.
 */
describe("a layout that names the retired task card (0.69)", () => {
  /** The 0.69 default as a 0.69 build stored it: the task card first, the background panel last. */
  function stored069(): Record<string, unknown> & { panels: Record<string, unknown> } {
    const raw = JSON.parse(JSON.stringify(defaultLayout()));
    const right = [
      ["conversation.tasks", "open"], ["conversation.changes", "open"], ["activity.tools", "open"],
      ["conversation.context", "open"], ["conversation.environment", "open"], ["conversation.connectors", "minimized"],
      ["activity.tokens", "open"], ["activity.memory", "open"], ["activity.fusion", "open"],
      ["activity.machine", "minimized"], ["activity.jobs", "open"],
    ];
    right.forEach(([id, mode], order) => (raw.panels[id] = { zone: "right", order, mode }));
    return raw;
  }

  it("migrates the untouched 0.69 default to today's order and preserves a changed panel choice", () => {
    const read = parseLayout(stored069());
    expect(read.panels).toEqual(defaultLayout().panels);
    expect(read.tabGroups).toEqual(defaultLayout().tabGroups);
    expect(Object.keys(read.panels)).not.toContain("conversation.tasks");

    const arranged = stored069();
    arranged.panels["activity.tools"] = { zone: "bottom", order: 0, mode: "open" };
    const kept = parseLayout(arranged);
    expect(kept.panels["activity.tools"]).toEqual({ zone: "bottom", order: 0, mode: "open" });
    // The merged card keeps the task card's place at the top: a person's change elsewhere is not
    // undone by the new default.
    expect(kept.panels["activity.jobs"]).toEqual({ zone: "right", order: 0, mode: "open" });
  });

  it("gives the heir the task card's place, mode, tab group and half of a split when it was earlier", () => {
    const raw = stored069();
    raw.panels["conversation.tasks"] = { zone: "right", order: 0, mode: "minimized" };
    raw.tabGroups = [["conversation.tasks", "conversation.changes"], ["activity.tokens", "activity.memory", "activity.fusion"]];
    raw.activeTabs = { "conversation.tasks": "conversation.tasks", "activity.tokens": "activity.tokens" };
    raw.splits = { right: { orientation: "vertical", ratio: 0.6, second: ["conversation.tasks", "conversation.changes"] } };

    const read = parseLayout(raw);

    expect(read.panels["activity.jobs"]).toEqual({ zone: "right", order: 0, mode: "minimized" });
    expect(read.tabGroups).toEqual([["activity.jobs", "conversation.changes"], ["activity.tokens", "activity.memory", "activity.fusion"]]);
    expect(read.activeTabs["activity.jobs"]).toBe("activity.jobs");
    expect(read.splits.right?.second.sort()).toEqual(["activity.jobs", "conversation.changes"]);
  });

  it("gives the heir the task card's place when the background panel was closed", () => {
    const raw = stored069();
    raw.panels["conversation.tasks"] = { zone: "bottom", order: 0, mode: "open" };
    raw.panels["activity.jobs"] = { zone: "right", order: 3, mode: "closed" };
    expect(parseLayout(raw).panels["activity.jobs"]).toEqual({ zone: "bottom", order: 0, mode: "open" });
  });

  it("keeps the heir where it was when the task card was closed, or later, or in another zone", () => {
    for (const tasks of [
      { zone: "right", order: 0, mode: "closed" },
      { zone: "right", order: 12, mode: "open" },
      { zone: "bottom", order: 0, mode: "open" },
    ]) {
      const raw = stored069();
      raw.panels["conversation.tasks"] = tasks;
      raw.panels["activity.jobs"] = { zone: "right", order: 5, mode: "minimized" };
      raw.tabGroups = [["conversation.tasks", "conversation.changes"]];
      const read = parseLayout(raw);
      expect(read.panels["activity.jobs"]).toEqual({ zone: "right", order: 5, mode: "minimized" });
      // The retired card's group lost a member it can no longer have, and a group of one is no group.
      expect(read.tabGroups).toEqual([]);
    }
  });

  it("leaves a layout that never named the task card exactly as the reader would", () => {
    const raw = JSON.parse(JSON.stringify(defaultLayout())) as Record<string, unknown>;
    expect(foldRetiredPanels(raw)).toBe(raw);
  });
});
