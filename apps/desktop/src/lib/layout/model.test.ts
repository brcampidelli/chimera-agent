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
  it("keeps the approval card, spend warnings and a failed turn's error closable only to a minimised line", () => {
    // The owner confirmed five things: these three cards, plus Stop and the status bar (next test).
    expect([...NEVER_CLOSED].sort()).toEqual(["approval", "error", "notices"]);
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

describe("moving", () => {
  it("moves a section to another zone at the position asked, and renumbers both zones", () => {
    const layout = run({ type: "move", panel: "activity.tools", zone: "left", index: 0 });

    expect(panelsIn(layout, "left")).toEqual(["activity.tools", "sessions"]);
    expect(panelsIn(layout, "right")).not.toContain("activity.tools");
    const orders = (zone: "left" | "right") => panelsIn(layout, zone).map((id) => layout.panels[id].order);
    expect(orders("left")).toEqual([0, 1]);
    expect(orders("right")).toEqual([0, 1, 2, 3, 4]);
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
      expect(layout.panels[id]).toMatchObject({ zone: PANELS[id].zone, mode: "open" });
    }
    expect(Object.values(layout.regions).every((r) => r.visible)).toBe(true);
    expect(hiddenItems(layout)).toEqual([]);
    expect(layout.maximized).toBeNull();
  });
});
