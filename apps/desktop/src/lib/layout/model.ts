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
  | "chart"
  | "design"
  | "diff"
  | "verdict"
  | "receipt"
  | "approval"
  | "question"
  | "error";

export const CARD_KINDS: readonly CardKind[] = [
  "tools", "todo", "notices", "browser", "chart", "design", "diff", "verdict", "receipt", "approval", "question", "error",
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
  // The open conversation's side cards (C5). Last in the list on purpose: a layout stored before
  // they existed has the six panels above numbered 0..5 in their zones, so these take the places
  // after them and every panel the person arranged keeps the zone, order and mode it had.
  "conversation.environment": { zone: "right", movable: true, closable: true, maximizable: false },
  "conversation.context": { zone: "right", movable: true, closable: true, maximizable: false },
  "conversation.connectors": { zone: "right", movable: true, closable: true, maximizable: false },
  "conversation.changes": { zone: "right", movable: true, closable: true, maximizable: false },
  // "conversation.tasks" sat here until 0.69: it read the same background jobs as "activity.jobs" and
  // the right panel showed them twice. It was folded into that panel; a stored layout that names it
  // is read by `foldRetiredPanels` (`store.ts`). Removing it numbers the panels before it exactly as
  // before, so the legacy default below still recognises an untouched layout.
  viewer: { zone: "center", movable: false, closable: true, maximizable: true },
  // Minimises to one line of chips and never closes: it carries the "no OS sandbox" warning.
  "composer.config": { zone: "composer", movable: false, closable: false, maximizable: false },
  // The parallel batch's board (2026-10-17). Its home is the main column, under the conversation, where
  // it always was; it can be minimised to its header, closed, and moved to a dock and back. Hiding it
  // hides a view: the batch itself lives in `AgentBatchProvider` and only its Stop buttons stop it.
  // Last in the list on purpose, for the reason the side cards above are after the six panels: it takes
  // the centre's next place (after the viewer), so a layout stored before it existed reads back unchanged.
  "agents.batch": { zone: "center", movable: true, closable: true, maximizable: false },
} as const satisfies Record<string, PanelSpec>;

export type PanelId = keyof typeof PANELS;

export const PANEL_IDS = Object.keys(PANELS) as PanelId[];

/**
 * Panels a stored layout may name that no longer exist, and the panel that inherited each one's place.
 * `foldRetiredPanels` (`store.ts`) applies it before a stored layout is read.
 */
export const RETIRED_PANELS: Readonly<Record<string, PanelId>> = {
  // 0.69 had two cards reading the same jobs list, "Conversation tasks" and "Background jobs".
  "conversation.tasks": "activity.jobs",
};

/**
 * Cards that minimise and never close. The five things the owner confirmed must never disappear
 * are these three cards plus the Stop button and the status bar, which are not panels.
 *
 * - `approval`: a pending approval is the person's decision; closing it would decide without seeing.
 * - `question`: a pending user question needs an answer before the agent can continue.
 * - `notices`: spend and limit warnings cost money when they go unseen.
 * - `error`: a turn that failed must not look like a turn that finished.
 */
export const NEVER_CLOSED: ReadonlySet<CardKind> = new Set<CardKind>(["approval", "question", "notices", "error"]);

/** Where a moved panel may land. The centre and the composer are not drop targets, except that a movable
 *  panel whose home is the centre (the batch board) may always go back home (`canLandIn`). */
const DROP_ZONES: ReadonlySet<Zone> = new Set<Zone>(["left", "right", "bottom"]);

/** Whether `panel` may be moved to `zone`: a dock, or the centre for a panel that lives there. */
export function canLandIn(panel: PanelId, zone: Zone): boolean {
  if (!PANELS[panel].movable) return false;
  return DROP_ZONES.has(zone) || (zone === "center" && PANELS[panel].zone === "center");
}

/** Sizes in pixels, clamped so a region can neither vanish by dragging nor swallow the window. */
export const SIZE_LIMITS: Record<SizedRegion, { min: number; max: number; initial: number }> = {
  left: { min: 180, max: 420, initial: 240 },
  right: { min: 220, max: 480, initial: 288 },
  bottom: { min: 96, max: 400, initial: 160 },
  // `initial` is Tailwind's `w-md`, the width the viewer had before it could be dragged.
  viewer: { min: 280, max: 900, initial: 448 },
};

/** How wide the conversation runs in the centre. Each is a Tailwind max-width token, never a value. */
export type TranscriptWidth = "narrow" | "medium" | "wide";

export const TRANSCRIPT_WIDTHS: readonly TranscriptWidth[] = ["narrow", "medium", "wide"];

/** Written out in full so Tailwind finds the classes in the source. `medium` is the width the
 *  conversation always had. */
export const TRANSCRIPT_WIDTH_CLASS: Record<TranscriptWidth, string> = {
  narrow: "max-w-2xl",
  medium: "max-w-3xl",
  wide: "max-w-5xl",
};

/**
 * Card presets: every card kind's open-or-minimised preference set in one step.
 *
 * Compact minimises the cards that are a record of work already done (the tool list, the receipt, the
 * browser) and opens everything else; Detailed opens every kind. Neither ever minimises the three kinds
 * that never close (`NEVER_CLOSED`): an approval or user question waiting, a spend warning and a failed
 * turn's error stay open, because a preset someone picked for tidiness must not hide a decision or failure.
 * The check is made here, on every kind, rather than trusted to the list below staying short.
 */
export type CardPreset = "compact" | "detailed";

const COMPACT_MINIMIZED: ReadonlySet<CardKind> = new Set<CardKind>(["tools", "receipt", "browser"]);

/** The `card-pref` actions a preset is made of, one per card kind. */
export function cardPresetActions(name: CardPreset): LayoutAction[] {
  return CARD_KINDS.map((kind): LayoutAction => ({
    type: "card-pref",
    kind,
    mode:
      name === "compact" && COMPACT_MINIMIZED.has(kind) && !NEVER_CLOSED.has(kind) ? "minimized" : "open",
  }));
}

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

export type DockZone = Extract<Zone, "left" | "right" | "bottom">;
export type SplitOrientation = "horizontal" | "vertical";

/** Panels shown as one tab strip. Members remain ordinary panels so older layout behaviour is intact. */
export type TabGroup = PanelId[];
export type PanelTabGroups = TabGroup[];
export type ActiveTabs = Partial<Record<PanelId, PanelId>>;
/**
 * One dock divided in two. `horizontal` puts the halves side by side, `vertical` one above the other.
 * `ratio` is the first half's share of the dock, clamped to `SPLIT_LIMITS`. `second` lists the panels
 * drawn in the second half; everything else stays in the first, so a panel that is moved in, reopened
 * or added by a newer build lands where the dock always put it. A tab group is in one half as a whole.
 */
export interface PanelSplit {
  orientation: SplitOrientation;
  ratio: number;
  second: PanelId[];
}
export type PanelSplits = Partial<Record<DockZone, PanelSplit>>;

/** Neither half of a split can be dragged smaller than a fifth of the dock: a half dragged to nothing
 *  would hide its panels without closing them, which is a panel lost. */
export const SPLIT_LIMITS = { min: 0.2, max: 0.8, initial: 0.5 } as const;

export function clampSplitRatio(ratio: number): number {
  return Math.min(SPLIT_LIMITS.max, Math.max(SPLIT_LIMITS.min, ratio));
}

/** The tab group a panel belongs to, if any. */
export function tabGroupOf(layout: Pick<LayoutCore, "tabGroups">, panel: PanelId): TabGroup | undefined {
  return layout.tabGroups.find((members) => members.includes(panel));
}

export interface LayoutCore {
  version: 1;
  regions: Record<Region, RegionState>;
  panels: Record<PanelId, PanelState>;
  cards: Record<CardKind, "open" | "minimized">;
  maximized: PanelId | null;
  transcriptWidth: TranscriptWidth;
  /** Empty for layouts written before tabbed panels were introduced. */
  tabGroups: PanelTabGroups;
  /** Group anchor to selected panel; groups without an entry select their first member. */
  activeTabs: ActiveTabs;
  /** A missing split leaves the dock as one unsplit column/row. */
  splits: PanelSplits;
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
  | { type: "card-preset"; name: CardPreset }
  | { type: "transcript-width"; width: TranscriptWidth }
  | { type: "group-tabs"; panels: PanelId[] }
  | { type: "ungroup-tab"; panel: PanelId }
  | { type: "activate-tab"; group: PanelId; panel: PanelId }
  /** Divide a dock (or undo it with `null`). `panel`, when given, starts in the second half with its
   *  whole tab group: a split with nothing in its second half would change nothing on screen. */
  | { type: "set-split"; zone: DockZone; orientation: SplitOrientation | null; panel?: PanelId }
  /** Put a panel (and its tab group) in the other half of its dock's split. */
  | { type: "split-move"; panel: PanelId }
  | { type: "resize-split"; zone: DockZone; ratio: number }
  /** Replace the whole layout, as read back from somewhere (the person's saved layout). The caller
   *  parses it first (`parseLayout`), so this never takes a value it has not checked. */
  | { type: "apply"; layout: Layout }
  | { type: "reset" };

/** Layouts one command away (phase 5). Focus is its own action because leaving it goes back exactly. */
export type LayoutPreset = "review" | "monitor";

/** The movable panels: the ones whose arrangement "the default right panel" is about. */
export const DOCK_PANEL_IDS = PANEL_IDS.filter((id) => PANELS[id].movable);

/**
 * The right panel's default order (ESTUDO-03 §4.3), most urgent first: the principle in `DESIGN.md`
 * is that the right is what the agent is doing right now. Until 0.68 the order was the historical one,
 * the order the panels were added in (`PANELS` above), which put the turn's token count first and the
 * conversation's own task list last.
 *
 * Tokens, memory and fusion all describe the last turn, the receipt the transcript already shows, so
 * they start as one tab group. The machine is about the computer, not the conversation: it starts
 * minimised at the end. What runs in the background comes first, where the conversation's own task
 * list was before it was folded into that panel (0.69 drew the same jobs twice, first and last). A
 * panel with nothing to show is not drawn at all, open or minimised (`useSilentPanels`), so first
 * place costs nothing while nothing runs.
 */
const DEFAULT_RIGHT: readonly { id: PanelId; mode: Mode }[] = [
  { id: "conversation.changes", mode: "open" },
  { id: "activity.jobs", mode: "open" },
  { id: "activity.tools", mode: "open" },
  { id: "conversation.context", mode: "open" },
  { id: "conversation.connectors", mode: "minimized" },
  { id: "activity.tokens", mode: "open" },
  { id: "activity.memory", mode: "open" },
  { id: "activity.fusion", mode: "open" },
  { id: "activity.machine", mode: "minimized" },
  { id: "conversation.environment", mode: "open" },
];

/** The "last turn" tab group the default starts with. */
export const DEFAULT_TAB_GROUPS: readonly TabGroup[] = [["activity.tokens", "activity.memory", "activity.fusion"]];

/** Where every panel sat in the default up to 0.68.1: each zone numbered in `PANELS` order, all open
 *  except the composer's pickers. Kept to recognise a layout nobody rearranged (`isLegacyDefault`). */
function legacyDefaultPanels(): Record<PanelId, PanelState> {
  const panels = {} as Record<PanelId, PanelState>;
  const counters: Partial<Record<Zone, number>> = {};
  for (const id of PANEL_IDS) {
    const zone = PANELS[id].zone;
    const order = counters[zone] ?? 0;
    counters[zone] = order + 1;
    panels[id] = { zone, order, mode: "open" };
  }
  // The composer's pickers start as their line of chips. Open, they were four rows stacked above the
  // box you type in (who runs, cost profile, model, style) on every screenful, for choices made once
  // a week; one line says the same and opens with a click.
  panels["composer.config"] = { ...panels["composer.config"], mode: "minimized" };
  return panels;
}

/**
 * Whether the dock panels are exactly where the old default put them, with no tabs and no split: the
 * layout of someone who never rearranged the right panel. Only the movable panels are compared, so
 * opening the composer's pickers or dragging a width does not count as arranging the panels.
 */
export function isLegacyDefault(layout: Pick<LayoutCore, "panels" | "tabGroups" | "splits">): boolean {
  if (layout.tabGroups.length > 0 || Object.keys(layout.splits).length > 0) return false;
  const legacy = legacyDefaultPanels();
  return DOCK_PANEL_IDS.every((id) => {
    const a = layout.panels[id];
    const b = legacy[id];
    return a.zone === b.zone && a.order === b.order && a.mode === b.mode;
  });
}

/** The 0.69 default after the retired task card has been folded into background jobs. */
function isLegacy069Default(layout: Pick<LayoutCore, "panels" | "tabGroups" | "activeTabs" | "splits">): boolean {
  if (Object.keys(layout.splits).length > 0) return false;
  if (JSON.stringify(layout.tabGroups) !== JSON.stringify(DEFAULT_TAB_GROUPS)) return false;
  const expectedActiveTabs = Object.fromEntries(DEFAULT_TAB_GROUPS.map((group) => [group[0], group[0]]));
  if (JSON.stringify(layout.activeTabs) !== JSON.stringify(expectedActiveTabs)) return false;
  const expected: readonly { id: PanelId; mode: Mode }[] = [
    { id: "activity.jobs", mode: "open" },
    { id: "conversation.changes", mode: "open" },
    { id: "activity.tools", mode: "open" },
    { id: "conversation.context", mode: "open" },
    { id: "conversation.environment", mode: "open" },
    { id: "conversation.connectors", mode: "minimized" },
    { id: "activity.tokens", mode: "open" },
    { id: "activity.memory", mode: "open" },
    { id: "activity.fusion", mode: "open" },
    { id: "activity.machine", mode: "minimized" },
  ];
  const rightIsUntouched = expected.every(({ id, mode }, order) => {
    const panel = layout.panels[id];
    return panel.zone === "right" && panel.order === order && panel.mode === mode;
  });
  const batch = layout.panels["agents.batch"];
  return rightIsUntouched && batch.zone === "center" && batch.order === 1 && batch.mode === "open";
}

/**
 * The current default arrangement put on a layout nobody rearranged; any other layout comes back as
 * the same object. Regions, widths, card preferences and the composer's pickers are the person's and
 * are kept either way. Idempotent by construction: the new arrangement is not the old one.
 */
export function migrateDefaultArrangement<T extends LayoutCore>(layout: T): T {
  if (!isLegacyDefault(layout) && !isLegacy069Default(layout)) return layout;
  const fresh = defaultLayout();
  const panels = { ...layout.panels };
  for (const id of DOCK_PANEL_IDS) panels[id] = fresh.panels[id];
  return { ...layout, panels, tabGroups: fresh.tabGroups, activeTabs: fresh.activeTabs, splits: {} };
}

export function defaultLayout(): Layout {
  const panels = legacyDefaultPanels();
  DEFAULT_RIGHT.forEach(({ id, mode }, order) => {
    panels[id] = { zone: "right", order, mode };
  });
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
    transcriptWidth: "medium",
    tabGroups: DEFAULT_TAB_GROUPS.map((group) => [...group]),
    // Named, not left to "the first member": a stored layout reads its groups back with an entry each,
    // and the default has to read back as itself.
    activeTabs: Object.fromEntries(DEFAULT_TAB_GROUPS.map((group) => [group[0], group[0]])),
    splits: {},
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
    transcriptWidth: layout.transcriptWidth,
    tabGroups: layout.tabGroups,
    activeTabs: layout.activeTabs,
    splits: layout.splits,
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

/** A closed or relocated panel stops belonging to its old tab strip and to its old half of a split
 *  (a panel reopened or moved back lands in the first half, where the dock always put it). */
function detach(layout: Layout, panel: PanelId): Pick<Layout, "tabGroups" | "activeTabs" | "splits"> {
  const zone = layout.panels[panel].zone;
  const split = DROP_ZONES.has(zone) ? layout.splits[zone as DockZone] : undefined;
  const splits =
    split && split.second.includes(panel)
      ? { ...layout.splits, [zone]: { ...split, second: split.second.filter((id) => id !== panel) } }
      : layout.splits;
  return { ...detachTab(layout, panel), splits };
}

/** A panel leaving its tab strip, which dissolves when one member is left. */
function detachTab(layout: Layout, panel: PanelId): Pick<Layout, "tabGroups" | "activeTabs"> {
  const group = layout.tabGroups.find((members) => members.includes(panel));
  if (!group) return { tabGroups: layout.tabGroups, activeTabs: layout.activeTabs };
  const remaining = group.filter((id) => id !== panel);
  const tabGroups = layout.tabGroups.filter((members) => members !== group);
  const activeTabs = { ...layout.activeTabs };
  delete activeTabs[group[0]];
  if (remaining.length > 1) {
    tabGroups.push(remaining);
    const oldActive = layout.activeTabs[group[0]];
    activeTabs[remaining[0]] = oldActive && remaining.includes(oldActive) ? oldActive : remaining[0];
  }
  return { tabGroups, activeTabs };
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
      const detached = action.mode === "closed" ? detach(layout, action.panel) : {};
      return { ...layout, panels, maximized, ...detached };
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
      if (!canLandIn(action.panel, action.zone)) return layout;
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
      // Dropping into a hidden side region shows it: a panel dropped out of sight is a panel lost. The
      // centre is not a region that hides.
      const region = DROP_ZONES.has(action.zone) ? (action.zone as Exclude<Zone, "center" | "composer">) : null;
      const regions =
        region === null || layout.regions[region].visible
          ? layout.regions
          : { ...layout.regions, [region]: { ...layout.regions[region], visible: true } };
      const detached = from !== action.zone ? detach(layout, action.panel) : {};
      return { ...layout, panels, regions, ...detached };
    }
    case "maximize": {
      if (action.panel !== null) {
        if (!PANELS[action.panel].maximizable || layout.panels[action.panel].mode === "closed") return layout;
      }
      if (layout.maximized === action.panel) return layout;
      return { ...layout, maximized: action.panel };
    }
    case "toggle-focus": {
      // The width is a reading preference, not part of the arrangement focus mode takes away: one
      // chosen while focused (Settings or the palette) is kept on the way out, not quietly undone.
      if (layout.beforeFocus) {
        return { ...layout.beforeFocus, transcriptWidth: layout.transcriptWidth, beforeFocus: null };
      }
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
    case "card-preset": {
      // One step to undo, made of the same actions the card corners send one at a time.
      return cardPresetActions(action.name).reduce((acc, step) => applyLayout(acc, step), layout);
    }
    case "transcript-width": {
      if (!TRANSCRIPT_WIDTHS.includes(action.width) || layout.transcriptWidth === action.width) return layout;
      return { ...layout, transcriptWidth: action.width };
    }
    case "group-tabs": {
      const selected = [...new Set(action.panels)];
      if (selected.length < 2 || selected.some((id) => !PANELS[id].movable)) return layout;
      const zone = layout.panels[selected[0]].zone;
      if (!DROP_ZONES.has(zone as DockZone) || selected.some((id) => layout.panels[id].zone !== zone || layout.panels[id].mode === "closed")) return layout;
      const merged = new Set(selected);
      const absorbed = layout.tabGroups.filter((group) => group.some((id) => merged.has(id)));
      for (const group of absorbed) group.forEach((id) => merged.add(id));
      const members = [...merged].sort((a, b) => layout.panels[a].order - layout.panels[b].order);
      const tabGroups = [...layout.tabGroups.filter((group) => !absorbed.includes(group)), members];
      const activeTabs = { ...layout.activeTabs };
      for (const group of absorbed) delete activeTabs[group[0]];
      activeTabs[members[0]] = selected[selected.length - 1];
      // A tab group sits in one half of a split as a whole: the half of the panel it was grouped onto.
      const split = layout.splits[zone as DockZone];
      if (!split) return { ...layout, tabGroups, activeTabs };
      const inSecond = split.second.includes(selected[0]);
      const rest = split.second.filter((id) => !merged.has(id));
      const second = inSecond ? [...rest, ...members] : rest;
      return { ...layout, tabGroups, activeTabs, splits: { ...layout.splits, [zone]: { ...split, second } } };
    }
    case "ungroup-tab": {
      if (!layout.tabGroups.some((members) => members.includes(action.panel))) return layout;
      return { ...layout, ...detachTab(layout, action.panel) };
    }
    case "activate-tab": {
      const group = layout.tabGroups.find((members) => members[0] === action.group);
      if (!group?.includes(action.panel) || layout.activeTabs[action.group] === action.panel) return layout;
      return { ...layout, activeTabs: { ...layout.activeTabs, [action.group]: action.panel } };
    }
    case "set-split": {
      if (!DROP_ZONES.has(action.zone)) return layout;
      if (action.orientation === null) {
        if (!layout.splits[action.zone]) return layout;
        const splits = { ...layout.splits };
        delete splits[action.zone];
        return { ...layout, splits };
      }
      const current = layout.splits[action.zone];
      let second = current?.second ?? [];
      if (action.panel !== undefined) {
        const state = layout.panels[action.panel];
        if (!PANELS[action.panel].movable || state.zone !== action.zone || state.mode === "closed") return layout;
        const members = tabGroupOf(layout, action.panel) ?? [action.panel];
        second = [...second.filter((id) => !members.includes(id)), ...members];
      }
      if (current?.orientation === action.orientation && second === current.second) return layout;
      const ratio = current?.ratio ?? SPLIT_LIMITS.initial;
      return { ...layout, splits: { ...layout.splits, [action.zone]: { orientation: action.orientation, ratio, second } } };
    }
    case "split-move": {
      const zone = layout.panels[action.panel].zone;
      const split = DROP_ZONES.has(zone) ? layout.splits[zone as DockZone] : undefined;
      if (!split || !PANELS[action.panel].movable || layout.panels[action.panel].mode === "closed") return layout;
      const members = tabGroupOf(layout, action.panel) ?? [action.panel];
      const rest = split.second.filter((id) => !members.includes(id));
      const second = split.second.includes(action.panel) ? rest : [...rest, ...members];
      return { ...layout, splits: { ...layout.splits, [zone]: { ...split, second } } };
    }
    case "resize-split": {
      const current = layout.splits[action.zone];
      if (!current || !Number.isFinite(action.ratio)) return layout;
      const ratio = clampSplitRatio(action.ratio);
      if (current.ratio === ratio) return layout;
      return { ...layout, splits: { ...layout.splits, [action.zone]: { ...current, ratio } } };
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
