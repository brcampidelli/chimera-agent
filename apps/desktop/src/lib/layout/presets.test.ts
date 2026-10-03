import { describe, expect, it } from "vitest";

import {
  CARD_KINDS,
  NEVER_CLOSED,
  TRANSCRIPT_WIDTH_CLASS,
  applyLayout,
  cardPresetActions,
  defaultLayout,
  type Layout,
  type LayoutAction,
  type TranscriptWidth,
} from "@/lib/layout/model";
import { parseLayout } from "@/lib/layout/store";

/**
 * The card presets and the conversation's width (study 29, P1.7 and P1.2): both live in the layout
 * model, so both are tested where they are enforced, with no screen.
 */
function run(start: Layout, ...actions: LayoutAction[]): Layout {
  return actions.reduce(applyLayout, start);
}

/** A layout where the person minimised every kind of card by hand, including the ones that never close. */
function allMinimized(): Layout {
  return run(defaultLayout(), ...CARD_KINDS.map((kind): LayoutAction => ({ type: "card-pref", kind, mode: "minimized" })));
}

describe("the card presets", () => {
  it("never minimizes an approval, a spend warning or a failed turn's error in the compact preset", () => {
    const compact = applyLayout(defaultLayout(), { type: "card-preset", name: "compact" });
    for (const kind of NEVER_CLOSED) expect(compact.cards[kind]).toBe("open");
    // And it opens them again when someone had minimised them by hand: a preset for tidiness is not
    // allowed to leave a decision folded away.
    const fromMinimized = applyLayout(allMinimized(), { type: "card-preset", name: "compact" });
    for (const kind of NEVER_CLOSED) expect(fromMinimized.cards[kind]).toBe("open");
    for (const action of cardPresetActions("compact")) {
      if (action.type === "card-pref" && NEVER_CLOSED.has(action.kind)) expect(action.mode).toBe("open");
    }
  });

  it("minimizes the tool list, the receipt and the browser in the compact preset and opens the rest", () => {
    const compact = applyLayout(allMinimized(), { type: "card-preset", name: "compact" });
    expect(compact.cards.tools).toBe("minimized");
    expect(compact.cards.receipt).toBe("minimized");
    expect(compact.cards.browser).toBe("minimized");
    for (const kind of CARD_KINDS.filter((k) => !["tools", "receipt", "browser"].includes(k))) {
      expect(compact.cards[kind]).toBe("open");
    }
  });

  it("opens every kind of card in the detailed preset", () => {
    const detailed = applyLayout(allMinimized(), { type: "card-preset", name: "detailed" });
    for (const kind of CARD_KINDS) expect(detailed.cards[kind]).toBe("open");
  });

  it("is built only from the card-pref actions the card corners already send", () => {
    for (const name of ["compact", "detailed"] as const) {
      const actions = cardPresetActions(name);
      expect(actions.every((a) => a.type === "card-pref")).toBe(true);
      expect(actions).toHaveLength(CARD_KINDS.length);
    }
  });

  it("changes nothing else in the layout, and returns the same object when there is nothing to change", () => {
    const start = run(defaultLayout(), { type: "set-region", region: "left", visible: false });
    const compact = applyLayout(start, { type: "card-preset", name: "compact" });
    expect(compact.regions).toBe(start.regions);
    expect(compact.panels).toBe(start.panels);
    // Already detailed (the default): refused by identity, so it is not a step to undo.
    const fresh = defaultLayout();
    expect(applyLayout(fresh, { type: "card-preset", name: "detailed" })).toBe(fresh);
    const twice = applyLayout(compact, { type: "card-preset", name: "compact" });
    expect(twice).toBe(compact);
  });
});

describe("the conversation's width", () => {
  it("starts at medium, the width the conversation always had", () => {
    expect(defaultLayout().transcriptWidth).toBe("medium");
    expect(TRANSCRIPT_WIDTH_CLASS.medium).toBe("max-w-3xl");
  });

  it("maps every width to a Tailwind max-width token, never an arbitrary value", () => {
    for (const cls of Object.values(TRANSCRIPT_WIDTH_CLASS)) expect(cls).toMatch(/^max-w-[a-z0-9]+$/);
  });

  it("changes with its action, refuses a width it does not know, and goes back on reset", () => {
    const wide = applyLayout(defaultLayout(), { type: "transcript-width", width: "wide" });
    expect(wide.transcriptWidth).toBe("wide");
    expect(applyLayout(wide, { type: "transcript-width", width: "wide" })).toBe(wide);
    // A value that crossed a JSON boundary is not trusted for its type.
    expect(applyLayout(wide, { type: "transcript-width", width: "huge" as TranscriptWidth })).toBe(wide);
    expect(applyLayout(wide, { type: "reset" }).transcriptWidth).toBe("medium");
  });

  it("survives the layout presets and comes back exactly from focus mode", () => {
    const wide = applyLayout(defaultLayout(), { type: "transcript-width", width: "wide" });
    expect(applyLayout(wide, { type: "preset", name: "review" }).transcriptWidth).toBe("wide");
    const focused = applyLayout(wide, { type: "toggle-focus" });
    expect(applyLayout(focused, { type: "toggle-focus" }).transcriptWidth).toBe("wide");
  });

  it("keeps a width chosen during focus mode when focus mode is left", () => {
    const focused = applyLayout(defaultLayout(), { type: "toggle-focus" });
    const narrowed = applyLayout(focused, { type: "transcript-width", width: "narrow" });
    const left = applyLayout(narrowed, { type: "toggle-focus" });
    expect(left.transcriptWidth).toBe("narrow");
    // Everything else focus mode changed still comes back exactly.
    expect(left.regions).toEqual(defaultLayout().regions);
    expect(left.beforeFocus).toBeNull();
  });

  it("reads a stored layout from before the width existed as medium, and keeps a stored width", () => {
    const old: Record<string, unknown> = { ...defaultLayout() };
    delete old.transcriptWidth;
    expect(parseLayout(old).transcriptWidth).toBe("medium");
    expect(parseLayout({ ...defaultLayout(), transcriptWidth: "enormous" }).transcriptWidth).toBe("medium");
    expect(parseLayout({ ...defaultLayout(), transcriptWidth: "narrow" }).transcriptWidth).toBe("narrow");
  });
});
