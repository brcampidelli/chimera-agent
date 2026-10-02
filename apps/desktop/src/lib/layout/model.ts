/**
 * The screen's layout as one value, and the one function that changes it.
 *
 * The owner asked on 2026-09-29 for a screen where anything can be minimised, maximised, closed,
 * dragged, resized and brought back, and approved the plan for it in eight phases. This is phase 0:
 * the model every later phase draws from. It changes nothing on screen by itself.
 *
 * **Why one value and a pure function.** Every button, drag and shortcut is an action applied by
 * `applyLayout`. Three things come with that for free: undo (keep the previous value), "restore the
 * default layout" (replace it), and tests that need no screen.
 *
 * **Why the rules live here and not in the buttons.** Some things must never disappear, because
 * hiding them would leave the person not knowing what the agent is doing, or unable to stop it. A
 * rule enforced by a button is a rule the next button forgets. An action this function refuses
 * returns the SAME object, so a caller can tell "refused" from "applied" by identity.
 *
 * What is never hidden, and how, is decided in `NEVER_CLOSED` below and in what the registry leaves
 * out: the Stop button and the status bar are not panels at all, so no action can reach them.
 */

/** Where a panel can sit. `center` holds the conversation and the file viewer. */
export type Zone = "left" | "center" | "right" | "bottom" | "composer";

/** The regions of the screen. All but `viewer` can be shown or hidden as a whole; the file viewer
 *  comes and goes with the file that is open, so the layout keeps only its width. */
export type Region = "rail" | "left" | "right" | "bottom" | "viewer";

/** The regions the layout itself hides and shows. */
export type HideableRegion = Exclude<Region, "viewer">;

/** The regions with a width the person can drag. */
export type SizedRegion = Exclude<Region, "rail">;

export type Mode = "open" | "minimized" | "closed";

/** A kind of card in the conversation. Its preference (open or minimised) is kept per kind. */
export type CardKind =
  | "tools"
  | "todo"
  | "notices"
  | "browser"
  | "diff"
  | "verdict"
  | "receipt"
  | "approval"
  | "error";

export const CARD_KINDS: readonly CardKind[] = [
  "tools", "todo", "notices", "browser", "diff", "verdict", "receipt", "approval", "error",
];

interface PanelSpec {
  /** Where it starts, and where "restore the default layout" puts it back. */
  zone: Zone;
  /** It can be dragged between the side regions and the bottom dock. */
  movable: boolean;
  /** It can be closed. False keeps it on screen, at most minimised. */
  closable: boolean;
  maximizable: boolean;
}

/** Every panel of the Code screen. The Stop button and the status bar are deliberately absent, and so
 *  is the agent's state line at the top of the right panel (phase 4 took it out of the list): it is the
 *  agent's state, which never disappears, so nothing may move or close it.
 *
 *  The conversation list stays home: it is drawn by the Code screen with that screen's own props, and
 *  the way to put it away is hiding the left region (phase 1). */
export const PANELS = {
  sessions: { zone: "left", movable: false, closable: false, maximizable: false },
  "activity.tools": { zone: "right", movable: true, closable: true, maximizable: true },
  "activity.tokens": { zone: "right", movable: true, closable: true, maximizable: false },
  "activity.memory": { zone: "right", movable: true, closable: true, maximizable: false },
  "activity.fusion": { zone: "right", movable: true, closable: true, maximizable: true },
  "activity.jobs": { zone: "right", movable: true, closable: true, maximizable: true },
  "activity.machine": { zone: "right", movable: true, closable: true, maximizable: true },
  viewer: { zone: "center", movable: false, closable: true, maximizable: true },
  // Minimises to one line of chips and never closes: it carries the "no OS sandbox" warning.
  "composer.config": { zone: "composer", movable: false, closable: false, maximizable: false },
} as const satisfies Record<string, PanelSpec>;

export type PanelId = keyof typeof PANELS;

export const PANEL_IDS = Object.keys(PANELS) as PanelId[];

/**
 * Cards that minimise and never close. The five things the owner confirmed must never disappear
 * are these three cards plus the Stop button and the status bar, which are not panels.
 *
 * - `approval`: a pending approval is the person's decision; closing it would decide without seeing.
 * - `notices`: spend and limit warnings cost money when they go unseen.
 * - `error`: a turn that failed must not look like a turn that finished.
 */
export const NEVER_CLOSED: ReadonlySet<CardKind> = new Set<CardKind>(["approval", "notices", "error"]);

/** Where a moved panel may land. The centre and the composer are not drop targets. */
const DROP_ZONES: ReadonlySet<Zone> = new Set<Zone>(["left", "right", "bottom"]);

/** Sizes in pixels, clamped so a region can neither vanish by dragging nor swallow the window. */
export const SIZE_LIMITS: Record<SizedRegion, { min: number; max: number; initial: number }> = {
  left: { min: 180, max: 420, initial: 240 },
  right: { min: 220, max: 480, initial: 288 },
  bottom: { min: 96, max: 400, initial: 160 },
  // `initial` is Tailwind's `w-md`, the width the viewer had before it could be dragged.
  viewer: { min: 280, max: 900, initial: 448 },
};

export interface PanelState {
  zone: Zone;
  order: number;
  mode: Mode;
}

export interface RegionState {
  visible: boolean;
  /** Pixels, or null for the rail, which has one width. */
  size: number | null;
}

export interface LayoutCore {
  version: 1;
  regions: Record<Region, RegionState>;
  panels: Record<PanelId, PanelState>;
  cards: Record<CardKind, "open" | "minimized">;
  maximized: PanelId | null;
}

export interface Layout extends LayoutCore {
  /** The layout before focus mode, so leaving it returns exactly there. */
  beforeFocus: LayoutCore | null;
}

export type LayoutAction =
  | { type: "set-mode"; panel: PanelId; mode: Mode }
  | { type: "set-region"; region: HideableRegion; visible: boolean }
  | { type: "resize"; region: SizedRegion; size: number }
  | { type: "move"; panel: PanelId; zone: Zone; index: number }
  | { type: "maximize"; panel: PanelId | null }
  | { type: "toggle-focus" }
  | { type: "card-pref"; kind: CardKind; mode: "open" | "minimized" }
  | { type: "show-all" }
  | { type: "preset"; name: LayoutPreset }
  /** Replace the whole layout, as read back from somewhere (the person's saved layout). The caller
   *  parses it first (`parseLayout`), so this never takes a value it has not checked. */
  | { type: "apply"; layout: Layout }
  | { type: "reset" };

/** Layouts one command away (phase 5). Focus is its own action because leaving it goes back exactly. */
export type LayoutPreset = "review" | "monitor";

export function defaultLayout(): Layout {
  const panels = {} as Record<PanelId, PanelState>;
  const counters: Partial<Record<Zone, number>> = {};
  for (const id of PANEL_IDS) {
    const zone = PANELS[id].zone;
    const order = counters[zone] ?? 0;
    counters[zone] = order + 1;
    panels[id] = { zone, order, mode: "open" };
  }
  const cards = {} as Record<CardKind, "open" | "minimized">;
  for (const kind of CARD_KINDS) cards[kind] = "open";
  return {
    version: 1,
    regions: {
      rail: { visible: true, size: null },
      left: { visible: true, size: SIZE_LIMITS.left.initial },
      right: { visible: true, size: SIZE_LIMITS.right.initial },
      // Empty until something is dragged to it; the region itself is on.
      bottom: { visible: true, size: SIZE_LIMITS.bottom.initial },
      viewer: { visible: true, size: SIZE_LIMITS.viewer.initial },
    },
    panels,
    cards,
    maximized: null,
    beforeFocus: null,
  };
}

/** Whether a card of this kind can be closed (per instance, in phase 3) rather than only minimised. */
export function canCloseCard(kind: CardKind): boolean {
  return !NEVER_CLOSED.has(kind);
}

/** The panels sitting in a zone that are on screen (open or minimised), in their order. */
export function panelsIn(layout: Layout, zone: Zone): PanelId[] {
  return PANEL_IDS.filter((id) => layout.panels[id].zone === zone && layout.panels[id].mode !== "closed")
    .sort((a, b) => layout.panels[a].order - layout.panels[b].order);
}

/** Every panel whose home is this zone, closed ones included, in their order. */
function allIn(layout: Layout, zone: Zone): PanelId[] {
  return PANEL_IDS.filter((id) => layout.panels[id].zone === zone)
    .sort((a, b) => layout.panels[a].order - layout.panels[b].order);
}

/** Something the person hid, and the action that brings it back. Minimised is not hidden. */
export type HiddenItem =
  | { kind: "region"; id: Region; restore: LayoutAction }
  | { kind: "panel"; id: PanelId; restore: LayoutAction };

export function hiddenItems(layout: Layout): HiddenItem[] {
  const out: HiddenItem[] = [];
  for (const region of ["left", "right", "rail"] as const) {
    if (!layout.regions[region].visible) {
      out.push({ kind: "region", id: region, restore: { type: "set-region", region, visible: true } });
    }
  }
  for (const id of PANEL_IDS) {
    if (layout.panels[id].mode === "closed") {
      out.push({ kind: "panel", id, restore: { type: "set-mode", panel: id, mode: "open" } });
    }
  }
  return out;
}

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(n)));
}

function core(layout: Layout): LayoutCore {
  return {
    version: layout.version,
    regions: layout.regions,
    panels: layout.panels,
    cards: layout.cards,
    maximized: layout.maximized,
  };
}

/** Renumber a zone 0..n-1 in its current order, so `order` never grows gaps or ties. */
function renumber(panels: Record<PanelId, PanelState>, zone: Zone, ids: PanelId[]): Record<PanelId, PanelState> {
  const next = { ...panels };
  ids.forEach((id, i) => {
    next[id] = { ...next[id], zone, order: i };
  });
  return next;
}

/**
 * Apply one action. A refused action returns `layout` itself, unchanged.
 *
 * Closing keeps a panel's zone and order, so reopening it puts it back where it was.
 */
export function applyLayout(layout: Layout, action: LayoutAction): Layout {
  switch (action.type) {
    case "set-mode": {
      const spec = PANELS[action.panel];
      const current = layout.panels[action.panel];
      if (action.mode === "closed" && !spec.closable) return layout;
      if (current.mode === action.mode) return layout;
      const panels = { ...layout.panels, [action.panel]: { ...current, mode: action.mode } };
      // A panel that is closed or minimised cannot also fill the screen.
      const maximized = action.mode !== "open" && layout.maximized === action.panel ? null : layout.maximized;
      return { ...layout, panels, maximized };
    }
    case "set-region": {
      // The type already keeps `viewer` out; this keeps it out of a value that crossed a JSON boundary.
      if ((action.region as Region) === "viewer") return layout;
      if (layout.regions[action.region].visible === action.visible) return layout;
      return {
        ...layout,
        regions: { ...layout.regions, [action.region]: { ...layout.regions[action.region], visible: action.visible } },
      };
    }
    case "resize": {
      const limits = SIZE_LIMITS[action.region];
      const size = clamp(action.size, limits.min, limits.max);
      if (layout.regions[action.region].size === size) return layout;
      return { ...layout, regions: { ...layout.regions, [action.region]: { ...layout.regions[action.region], size } } };
    }
    case "move": {
      if (!PANELS[action.panel].movable || !DROP_ZONES.has(action.zone)) return layout;
      const from = layout.panels[action.panel].zone;
      // `index` counts the panels the person SEES in the target zone. The whole zone, closed ones
      // included, is renumbered, so a closed panel there keeps a slot of its own and no two tie.
      const visible = panelsIn(layout, action.zone).filter((id) => id !== action.panel);
      const all = allIn(layout, action.zone).filter((id) => id !== action.panel);
      const before = visible[clamp(action.index, 0, visible.length)];
      all.splice(before === undefined ? all.length : all.indexOf(before), 0, action.panel);
      let panels = renumber(layout.panels, action.zone, all);
      // Moving is also how a closed panel comes back somewhere new.
      if (panels[action.panel].mode === "closed") panels = { ...panels, [action.panel]: { ...panels[action.panel], mode: "open" } };
      if (from !== action.zone) panels = renumber(panels, from, allIn({ ...layout, panels }, from));
      // Dropping into a hidden side region shows it: a panel dropped out of sight is a panel lost.
      const region = action.zone as Exclude<Zone, "center" | "composer">;
      const regions = layout.regions[region].visible
        ? layout.regions
        : { ...layout.regions, [region]: { ...layout.regions[region], visible: true } };
      return { ...layout, panels, regions };
    }
    case "maximize": {
      if (action.panel !== null) {
        if (!PANELS[action.panel].maximizable || layout.panels[action.panel].mode === "closed") return layout;
      }
      if (layout.maximized === action.panel) return layout;
      return { ...layout, maximized: action.panel };
    }
    case "toggle-focus": {
      if (layout.beforeFocus) return { ...layout.beforeFocus, beforeFocus: null };
      const regions = {
        ...layout.regions,
        rail: { ...layout.regions.rail, visible: false },
        left: { ...layout.regions.left, visible: false },
        right: { ...layout.regions.right, visible: false },
      };
      const panels = { ...layout.panels };
      // The composer's settings shrink to their line of chips; the input and Stop stay.
      panels["composer.config"] = { ...panels["composer.config"], mode: "minimized" };
      for (const id of panelsIn(layout, "bottom")) panels[id] = { ...panels[id], mode: "minimized" };
      return { ...layout, regions, panels, maximized: null, beforeFocus: core(layout) };
    }
    case "card-pref": {
      if (layout.cards[action.kind] === action.mode) return layout;
      return { ...layout, cards: { ...layout.cards, [action.kind]: action.mode } };
    }
    case "show-all": {
      const hidden = hiddenItems(layout);
      if (hidden.length === 0) return layout;
      return hidden.reduce((acc, item) => applyLayout(acc, item.restore), layout);
    }
    case "preset": {
      // Review: the file viewer as wide as it goes and the conversation list out of the way, for reading
      // a large diff. Monitor: the right panel as wide as it goes, for watching a long run.
      const regions =
        action.name === "review"
          ? {
              ...layout.regions,
              left: { ...layout.regions.left, visible: false },
              viewer: { ...layout.regions.viewer, size: SIZE_LIMITS.viewer.max },
            }
          : {
              ...layout.regions,
              right: { visible: true, size: SIZE_LIMITS.right.max },
            };
      return { ...layout, regions, maximized: null, beforeFocus: null };
    }
    case "apply":
      return action.layout;
    case "reset":
      return defaultLayout();
    default:
      return assertNever(action);
  }
}

function assertNever(value: never): never {
  throw new Error(`unhandled layout action: ${JSON.stringify(value)}`);
}
