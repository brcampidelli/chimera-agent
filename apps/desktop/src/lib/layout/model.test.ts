import { describe, expect, it } from "vitest";

import {
  CARD_KINDS,
  NEVER_CLOSED,
  PANEL_IDS,
  PANELS,
  applyLayout,
  canCloseCard,
  defaultLayout,
  hiddenItems,
  panelsIn,
  type Layout,
  type LayoutAction,
} from "@/lib/layout/model";

/**
 * The layout model of the dynamic screen (phase 0 of the plan the owner approved on 2026-09-29).
 *
 * The rules that matter most are the ones about what can never disappear, so they are tested where
 * they are enforced: in the one function every button, drag and shortcut goes through. A refused
 * action returns the SAME object, which is how a caller tells "refused" from "applied".
 */
function run(...actions: LayoutAction[]): Layout {
  return actions.reduce(applyLayout, defaultLayout());
}

describe("what never disappears", () => {
  it("keeps pending decisions, questions, spend warnings and failed turns closable only to a minimised line", () => {
    // A pending approval or user question needs an answer; the warnings and errors must also remain visible.
    expect([...NEVER_CLOSED].sort()).toEqual(["approval", "error", "notices", "question"]);
    expect(CARD_KINDS).toContain("question");
    for (const kind of NEVER_CLOSED) expect(canCloseCard(kind)).toBe(false);
    for (const kind of CARD_KINDS.filter((k) => !NEVER_CLOSED.has(k))) expect(canCloseCard(kind)).toBe(true);
  });

  it("gives no action a way to reach Stop or the status bar, because neither is a panel", () => {
    const ids: string[] = PANEL_IDS;
    // The agent's state line joined this list in phase 4: it is the agent's state, which never hides.
    for (const forbidden of ["stop", "statusbar", "status-bar", "composer.input", "conversation", "activity.status"]) {
      expect(ids).not.toContain(forbidden);
    }
  });

  it("refuses to close the composer's settings, which carry the no-sandbox warning", () => {
    const start = defaultLayout();
    const after = applyLayout(start, { type: "set-mode", panel: "composer.config", mode: "closed" });

    expect(after).toBe(start);
    expect(applyLayout(start, { type: "set-mode", panel: "composer.config", mode: "minimized" }).panels["composer.config"].mode).toBe("minimized");
  });
});

describe("closing and bringing back", () => {
  it("reopens a closed panel exactly where it was", () => {
    const moved = run({ type: "move", panel: "activity.tools", zone: "right", index: 3 });
    const home = moved.panels["activity.tools"];

    const back = [
      { type: "set-mode", panel: "activity.tools", mode: "closed" } as const,
      { type: "set-mode", panel: "activity.tools", mode: "open" } as const,
    ].reduce(applyLayout, moved);

    expect(back.panels["activity.tools"]).toEqual(home);
    expect(panelsIn(back, "right")).toEqual(panelsIn(moved, "right"));
  });

  it("lists what was hidden, each with the action that brings it back", () => {
    const layout = run(
      { type: "set-mode", panel: "activity.memory", mode: "closed" },
      { type: "set-region", region: "left", visible: false },
      // Minimised is not hidden: it is still on screen as a line.
      { type: "set-mode", panel: "activity.tokens", mode: "minimized" },
    );

    const hidden = hiddenItems(layout);
    expect(hidden.map((h) => `${h.kind}:${h.id}`)).toEqual(["region:left", "panel:activity.memory"]);

    const restored = hidden.reduce((acc, h) => applyLayout(acc, h.restore), layout);
    expect(hiddenItems(restored)).toEqual([]);
  });

  it("shows everything at once, and changes nothing when nothing is hidden", () => {
    const start = defaultLayout();
    expect(applyLayout(start, { type: "show-all" })).toBe(start);

    const layout = run(
      { type: "set-mode", panel: "activity.memory", mode: "closed" },
      { type: "set-region", region: "right", visible: false },
      { type: "set-region", region: "rail", visible: false },
      { type: "show-all" },
    );
    expect(hiddenItems(layout)).toEqual([]);
  });

  it("restores the default layout from anything", () => {
    const messy = run(
      { type: "set-region", region: "left", visible: false },
      { type: "move", panel: "activity.machine", zone: "bottom", index: 0 },
      { type: "resize", region: "right", size: 460 },
      { type: "toggle-focus" },
      { type: "reset" },
    );
    expect(messy).toEqual(defaultLayout());
  });
});
describe("tab groups and split panels", () => {
  it("groups panels from one region, changes the active tab, and removes the group after the last member leaves", () => {
    const grouped = applyLayout(defaultLayout(), { type: "group-tabs", panels: ["activity.tools", "activity.tokens"] });
    expect(grouped.tabGroups).toEqual([["activity.tools", "activity.tokens"]]);
    expect(grouped.activeTabs["activity.tools"]).toBe("activity.tokens");

    const activated = applyLayout(grouped, { type: "activate-tab", group: "activity.tools", panel: "activity.tools" });
    expect(activated.activeTabs["activity.tools"]).toBe("activity.tools");
    const ungrouped = applyLayout(activated, { type: "ungroup-tab", panel: "activity.tokens" });
    expect(ungrouped.tabGroups).toEqual([]);
    expect(ungrouped.activeTabs).toEqual({});
  });

  it("refuses to group panels from different regions and detaches a panel moved out of its group", () => {
    const layout = applyLayout(defaultLayout(), { type: "group-tabs", panels: ["activity.tools", "activity.tokens"] });
    expect(applyLayout(layout, { type: "group-tabs", panels: ["activity.tools", "sessions"] })).toBe(layout);
    const moved = applyLayout(layout, { type: "move", panel: "activity.tokens", zone: "bottom", index: 0 });
    expect(moved.tabGroups).toEqual([]);
    expect(moved.panels["activity.tokens"].zone).toBe("bottom");
  });

  it("persists split direction and a bounded divider ratio through layout actions", () => {
    const split = applyLayout(defaultLayout(), { type: "set-split", zone: "right", orientation: "horizontal" });
    const resized = applyLayout(split, { type: "resize-split", zone: "right", ratio: 0.68 });
    expect(resized.splits.right).toEqual({ orientation: "horizontal", ratio: 0.68, second: [] });
    expect(applyLayout(resized, { type: "resize-split", zone: "right", ratio: 0.01 }).splits.right?.ratio).toBe(0.2);
    expect(applyLayout(resized, { type: "resize-split", zone: "right", ratio: Number.NaN })).toBe(resized);
    expect(applyLayout(resized, { type: "resize-split", zone: "right", ratio: 2 }).splits.right?.ratio).toBe(0.8);
    expect(applyLayout(resized, { type: "set-split", zone: "right", orientation: null }).splits.right).toBeUndefined();
  });
});

describe("halves of a split", () => {
  it("starts the panel it was split from in the second half, with its whole tab group", () => {
    const grouped = applyLayout(defaultLayout(), { type: "group-tabs", panels: ["activity.tools", "activity.tokens"] });
    const split = applyLayout(grouped, { type: "set-split", zone: "right", orientation: "vertical", panel: "activity.tokens" });
    expect(split.splits.right?.second.sort()).toEqual(["activity.tokens", "activity.tools"]);
    const back = applyLayout(split, { type: "split-move", panel: "activity.tools" });
    expect(back.splits.right?.second).toEqual([]);
  });

  it("refuses to split from a panel that is not in that dock", () => {
    const layout = defaultLayout();
    expect(applyLayout(layout, { type: "set-split", zone: "left", orientation: "vertical", panel: "activity.tools" })).toBe(layout);
    expect(applyLayout(layout, { type: "split-move", panel: "activity.tools" })).toBe(layout);
  });

  it("lets a panel that is closed or moved away leave its half, so it comes back in the first", () => {
    const split = applyLayout(defaultLayout(), { type: "set-split", zone: "right", orientation: "horizontal", panel: "activity.jobs" });
    const closed = applyLayout(split, { type: "set-mode", panel: "activity.jobs", mode: "closed" });
    expect(closed.splits.right?.second).toEqual([]);
    const moved = applyLayout(split, { type: "move", panel: "activity.jobs", zone: "bottom", index: 0 });
    expect(moved.splits.right?.second).toEqual([]);
    // Reordering inside the same dock keeps the half.
    const reordered = applyLayout(split, { type: "move", panel: "activity.jobs", zone: "right", index: 0 });
    expect(reordered.splits.right?.second).toEqual(["activity.jobs"]);
  });

  it("puts a group in the half of the panel it was grouped onto", () => {
    const split = applyLayout(defaultLayout(), { type: "set-split", zone: "right", orientation: "horizontal", panel: "activity.jobs" });
    const grouped = applyLayout(split, { type: "group-tabs", panels: ["activity.jobs", "activity.tools"] });
    expect(grouped.splits.right?.second.sort()).toEqual(["activity.jobs", "activity.tools"]);
    const pulledBack = applyLayout(split, { type: "group-tabs", panels: ["activity.tools", "activity.jobs"] });
    expect(pulledBack.splits.right?.second).toEqual([]);
  });

  it("is cleared by restoring the default layout", () => {
    const actions = [
      { type: "group-tabs", panels: ["activity.tools", "activity.tokens"] },
      { type: "set-split", zone: "right", orientation: "horizontal", panel: "activity.jobs" },
      { type: "reset" },
    ] satisfies LayoutAction[];
    expect(actions.reduce(applyLayout, defaultLayout())).toEqual(defaultLayout());
  });
});

describe("moving", () => {
  it("moves a section to another zone at the position asked, and renumbers both zones", () => {
    const layout = run({ type: "move", panel: "activity.tools", zone: "left", index: 0 });

    expect(panelsIn(layout, "left")).toEqual(["activity.tools", "sessions"]);
    expect(panelsIn(layout, "right")).not.toContain("activity.tools");
    const orders = (zone: "left" | "right") => panelsIn(layout, zone).map((id) => layout.panels[id].order);
    expect(orders("left")).toEqual([0, 1]);
    expect(orders("right")).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("counts the index among the panels the person sees, with a closed one still keeping its slot", () => {
    const layout = run(
      { type: "set-mode", panel: "activity.tokens", mode: "closed" },
      // Second VISIBLE position on the right: after tools, before memory (tokens, closed, is skipped).
      { type: "move", panel: "activity.machine", zone: "right", index: 1 },
    );
    expect(panelsIn(layout, "right").slice(0, 3)).toEqual(["activity.tools", "activity.machine", "activity.memory"]);
    const all = PANEL_IDS.filter((id) => layout.panels[id].zone === "right").map((id) => layout.panels[id].order);
    expect(new Set(all).size).toBe(all.length); // no two tie
  });

  it("refuses what cannot move and where nothing may land", () => {
    const start = defaultLayout();
    expect(applyLayout(start, { type: "move", panel: "viewer", zone: "right", index: 0 })).toBe(start);
    expect(applyLayout(start, { type: "move", panel: "composer.config", zone: "left", index: 0 })).toBe(start);
    expect(applyLayout(start, { type: "move", panel: "activity.tools", zone: "center", index: 0 })).toBe(start);
    expect(applyLayout(start, { type: "move", panel: "activity.tools", zone: "composer", index: 0 })).toBe(start);
  });

  it("shows a hidden side region when something is dropped into it, so nothing is dropped out of sight", () => {
    const layout = run(
      { type: "set-region", region: "left", visible: false },
      { type: "move", panel: "activity.jobs", zone: "left", index: 0 },
    );
    expect(layout.regions.left.visible).toBe(true);
  });

  it("brings a closed panel back open when it is moved", () => {
    const layout = run(
      { type: "set-mode", panel: "activity.fusion", mode: "closed" },
      { type: "move", panel: "activity.fusion", zone: "bottom", index: 0 },
    );
    expect(layout.panels["activity.fusion"]).toMatchObject({ zone: "bottom", mode: "open" });
  });
});

describe("maximise, focus and size", () => {
  it("maximises only what may be, and a minimised or closed panel stops filling the screen", () => {
    const start = defaultLayout();
    expect(applyLayout(start, { type: "maximize", panel: "activity.tokens" })).toBe(start);

    const max = applyLayout(start, { type: "maximize", panel: "activity.tools" });
    expect(max.maximized).toBe("activity.tools");
    expect(applyLayout(max, { type: "set-mode", panel: "activity.tools", mode: "minimized" }).maximized).toBeNull();
    expect(applyLayout(max, { type: "maximize", panel: null }).maximized).toBeNull();
  });

  it("focus hides the side regions and the rail, keeps the composer's input, and leaves exactly as it found things", () => {
    const before = run(
      { type: "move", panel: "activity.machine", zone: "bottom", index: 0 },
      { type: "resize", region: "left", size: 300 },
    );
    const focused = applyLayout(before, { type: "toggle-focus" });

    expect(focused.regions.left.visible).toBe(false);
    expect(focused.regions.right.visible).toBe(false);
    expect(focused.regions.rail.visible).toBe(false);
    expect(focused.panels["composer.config"].mode).toBe("minimized");
    expect(focused.panels["activity.machine"].mode).toBe("minimized");

    expect(applyLayout(focused, { type: "toggle-focus" })).toEqual(before);
  });

  it("clamps sizes so a region can neither vanish by dragging nor swallow the window", () => {
    expect(run({ type: "resize", region: "left", size: 10 }).regions.left.size).toBe(180);
    expect(run({ type: "resize", region: "right", size: 5000 }).regions.right.size).toBe(480);
    expect(run({ type: "resize", region: "bottom", size: 222.6 }).regions.bottom.size).toBe(223);
  });

  it("keeps a per-kind card preference, and changing it to what it is changes nothing", () => {
    const start = defaultLayout();
    expect(applyLayout(start, { type: "card-pref", kind: "tools", mode: "open" })).toBe(start);
    expect(applyLayout(start, { type: "card-pref", kind: "tools", mode: "minimized" }).cards.tools).toBe("minimized");
  });
});

describe("the default layout", () => {
  it("is today's screen: every panel open, in its home zone, and every region shown", () => {
    const layout = defaultLayout();
    for (const id of PANEL_IDS) {
      // The composer's pickers are the exception: they start as their line of chips, which is still
      // on screen and opens with a click — so nothing is hidden, and `hiddenItems` stays empty.
      const mode = id === "composer.config" ? "minimized" : "open";
      expect(layout.panels[id]).toMatchObject({ zone: PANELS[id].zone, mode });
    }
    expect(Object.values(layout.regions).every((r) => r.visible)).toBe(true);
    expect(hiddenItems(layout)).toEqual([]);
    expect(layout.maximized).toBeNull();
  });
});
