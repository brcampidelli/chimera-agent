import {
  CARD_KINDS,
  DOCK_PANEL_IDS,
  PANEL_IDS,
  PANELS,
  RETIRED_PANELS,
  SIZE_LIMITS,
  SPLIT_LIMITS,
  clampSplitRatio,
  defaultLayout,
  migrateDefaultArrangement,
  type ActiveTabs,
  type CardKind,
  type DockZone,
  type Layout,
  type LayoutCore,
  type Mode,
  type PanelId,
  type PanelSplits,
  type Region,
  type TabGroup,
  type TranscriptWidth,
  type Zone,
  TRANSCRIPT_WIDTHS,
} from "@/lib/layout/model";

/**
 * Where the layout is kept between launches, and how a stored one is read back.
 *
 * Local for now, one key per screen. The owner chose on 2026-09-29 to move it to the server in phase
 * 6, the way the project list already moved, so it survives a reinstall.
 *
 * The rule for reading is that a stored layout can never break the screen. Anything this code does not
 * recognise becomes the default: an unknown version, a value that is not an object, a zone that does
 * not exist. A panel the stored value does not mention (a newer build added it) takes its default
 * place; a panel the stored value mentions that no longer exists is dropped.
 */
export const STORAGE_KEY = "chimera.layout.v1.code";

const ZONES: ReadonlySet<string> = new Set<Zone>(["left", "center", "right", "bottom", "composer"]);
const MODES: ReadonlySet<string> = new Set<Mode>(["open", "minimized", "closed"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A stored panel's place when it is one this code can read, else null. */
function storedPlace(value: unknown): { zone: string; order: number; mode: string } | null {
  if (!isRecord(value)) return null;
  const { zone, order, mode } = value;
  if (typeof zone !== "string" || !ZONES.has(zone) || typeof mode !== "string" || !MODES.has(mode)) return null;
  if (typeof order !== "number" || !Number.isFinite(order)) return null;
  return { zone, order, mode };
}

/**
 * A stored layout with each retired panel (`RETIRED_PANELS`) handed to the panel that inherited it.
 *
 * Dropping the retired id would be enough for the reader below, which ignores ids it does not know.
 * It is not enough for the person: "Conversation tasks" (0.69) was the card at the TOP of the right
 * panel, and "Background jobs", which took over its content, was at the bottom. So the heir takes the
 * retired card's place, its mode, its tab group and its half of a split when the person could see the
 * retired card and either could not see the heir (closed) or had the retired card earlier in the same
 * zone. Otherwise the heir keeps its own place and the retired card simply goes.
 *
 * Two consequences, both on purpose. The 0.69 default (tasks first, jobs last) reads back as today's
 * default, which has the merged panel first. And the default of 0.68 and before (jobs at its old
 * place, tasks after it) reads back with jobs where it was, so `migrateDefaultArrangement` still
 * recognises a layout nobody touched.
 */
export function foldRetiredPanels(value: Record<string, unknown>): Record<string, unknown> {
  let out = value;
  for (const [retired, heir] of Object.entries(RETIRED_PANELS)) {
    const panels = isRecord(out.panels) ? out.panels : null;
    if (!panels || !(retired in panels)) continue;
    const gone = storedPlace(panels[retired]);
    const kept = storedPlace(panels[heir]);
    const inherits =
      gone !== null &&
      gone.mode !== "closed" &&
      (kept === null || kept.mode === "closed" || (kept.zone === gone.zone && gone.order < kept.order));
    const nextPanels: Record<string, unknown> = { ...panels };
    delete nextPanels[retired];
    if (!inherits) {
      out = { ...out, panels: nextPanels };
      continue;
    }
    nextPanels[heir] = { ...gone };
    // Every list that named the retired card names the heir instead, and the heir leaves the lists it
    // was in at its old place: a panel is in one tab group and one half of a split at most.
    const swap = (ids: unknown): unknown =>
      Array.isArray(ids) ? ids.filter((id) => id !== heir).map((id) => (id === retired ? heir : id)) : ids;
    const tabGroups = Array.isArray(out.tabGroups) ? out.tabGroups.map(swap) : out.tabGroups;
    const activeTabs = isRecord(out.activeTabs)
      ? Object.fromEntries(
          Object.entries(out.activeTabs)
            .filter(([anchor]) => anchor !== heir)
            .map(([anchor, selected]) => [anchor === retired ? heir : anchor, selected === retired ? heir : selected]),
        )
      : out.activeTabs;
    const splits = isRecord(out.splits)
      ? Object.fromEntries(
          Object.entries(out.splits).map(([zone, split]) => [zone, isRecord(split) ? { ...split, second: swap(split.second) } : split]),
        )
      : out.splits;
    const maximized = out.maximized === retired ? heir : out.maximized;
    out = { ...out, panels: nextPanels, tabGroups, activeTabs, splits, maximized };
  }
  return out;
}

function readCore(raw: unknown): LayoutCore | null {
  if (!isRecord(raw) || raw.version !== 1) return null;
  const value = foldRetiredPanels(raw);
  const base = defaultLayout();

  const regions = { ...base.regions };
  if (isRecord(value.regions)) {
    for (const region of Object.keys(regions) as Region[]) {
      const stored = value.regions[region];
      if (!isRecord(stored) || typeof stored.visible !== "boolean") continue;
      const limits = region === "rail" ? null : SIZE_LIMITS[region];
      const size =
        limits && typeof stored.size === "number" && Number.isFinite(stored.size)
          ? Math.min(limits.max, Math.max(limits.min, Math.round(stored.size)))
          : regions[region].size;
      regions[region] = { visible: stored.visible, size };
    }
  }

  const panels = { ...base.panels };
  const read = new Set<PanelId>();
  if (isRecord(value.panels)) {
    for (const id of PANEL_IDS) {
      const stored = value.panels[id];
      if (!isRecord(stored)) continue;
      const zone = typeof stored.zone === "string" && ZONES.has(stored.zone) ? (stored.zone as Zone) : null;
      const mode = typeof stored.mode === "string" && MODES.has(stored.mode) ? (stored.mode as Mode) : null;
      const order = typeof stored.order === "number" && Number.isFinite(stored.order) ? stored.order : null;
      if (zone === null || mode === null || order === null) continue;
      // A panel that cannot move stays home; one that cannot close is never read back closed.
      const home = PANELS[id].movable ? zone : PANELS[id].zone;
      panels[id] = { zone: home, order, mode: mode === "closed" && !PANELS[id].closable ? "open" : mode };
      read.add(id);
    }
  }
  // A movable panel the stored value does not mention (a newer build added it) goes open after
  // everything the person arranged in its zone, in the order the panels were added. Taking its place in
  // today's default instead would tie with, and could jump ahead of, a panel that was already there.
  const placed = new Set(read);
  for (const id of DOCK_PANEL_IDS) {
    if (read.has(id)) continue;
    const zone = PANELS[id].zone;
    const taken = [...placed].filter((other) => panels[other].zone === zone).map((other) => panels[other].order);
    panels[id] = { zone, order: taken.length === 0 ? 0 : Math.max(...taken) + 1, mode: "open" };
    placed.add(id);
  }

  const cards = { ...base.cards };
  if (isRecord(value.cards)) {
    for (const kind of CARD_KINDS) {
      const stored = value.cards[kind];
      if (stored === "open" || stored === "minimized") cards[kind as CardKind] = stored;
    }
  }

  const maximized =
    typeof value.maximized === "string" && (PANEL_IDS as string[]).includes(value.maximized)
      ? (value.maximized as PanelId)
      : null;

  // Added after layouts were already stored: one without it, or with a width this build does not
  // know, reads as the width the conversation always had.
  const transcriptWidth = (TRANSCRIPT_WIDTHS as readonly unknown[]).includes(value.transcriptWidth)
    ? (value.transcriptWidth as TranscriptWidth)
    : base.transcriptWidth;

  // Older version-1 layouts have no tabs or split metadata. Empty defaults keep their panel order and
  // visibility unchanged, so adding the feature never silently rearranges an existing workspace.
  const tabGroups: TabGroup[] = [];
  const assignedToGroup = new Set<PanelId>();
  if (Array.isArray(value.tabGroups)) {
    for (const candidate of value.tabGroups) {
      if (!Array.isArray(candidate)) continue;
      const members = [...new Set(candidate.filter((id): id is PanelId => typeof id === "string" && (PANEL_IDS as string[]).includes(id)))];
      if (members.length < 2 || members.some((id) => !PANELS[id].movable)) continue;
      const zone = panels[members[0]].zone;
      if (!(zone === "left" || zone === "right" || zone === "bottom") || members.some((id) => assignedToGroup.has(id) || panels[id].zone !== zone || panels[id].mode === "closed")) continue;
      members.forEach((id) => assignedToGroup.add(id));
      tabGroups.push(members);
    }
  }
  const activeTabs: ActiveTabs = {};
  if (isRecord(value.activeTabs)) {
    for (const group of tabGroups) {
      const anchor = group[0];
      const selected = value.activeTabs[anchor];
      activeTabs[anchor] = typeof selected === "string" && group.includes(selected as PanelId) ? (selected as PanelId) : anchor;
    }
  }
  const splits: PanelSplits = {};
  if (isRecord(value.splits)) {
    for (const zone of ["left", "right", "bottom"] as const satisfies readonly DockZone[]) {
      const split = value.splits[zone];
      if (!isRecord(split) || (split.orientation !== "horizontal" && split.orientation !== "vertical")) continue;
      const ratio =
        typeof split.ratio === "number" && Number.isFinite(split.ratio) ? clampSplitRatio(split.ratio) : SPLIT_LIMITS.initial;
      // The second half keeps only panels that are still in this dock and on screen; anything else it
      // named reads as being in the first half, where the dock would have put it without a split.
      const named = new Set(
        (Array.isArray(split.second) ? split.second : []).filter(
          (id): id is PanelId =>
            typeof id === "string" &&
            (PANEL_IDS as string[]).includes(id) &&
            PANELS[id as PanelId].movable &&
            panels[id as PanelId].zone === zone &&
            panels[id as PanelId].mode !== "closed",
        ),
      );
      // A tab group is in one half as a whole: the half its first member was in.
      for (const group of tabGroups) {
        if (panels[group[0]].zone !== zone) continue;
        const inSecond = named.has(group[0]);
        for (const id of group) {
          if (inSecond) named.add(id);
          else named.delete(id);
        }
      }
      splits[zone] = { orientation: split.orientation, ratio, second: [...named] };
    }
  }

  // Someone who never rearranged the dock panels gets today's default arrangement; anyone else keeps
  // theirs exactly (ESTUDO-03 §4.3).
  return migrateDefaultArrangement({ version: 1, regions, panels, cards, maximized, transcriptWidth, tabGroups, activeTabs, splits });
}

/** A stored value as a layout, or the default for anything not recognised. Never throws. */
export function parseLayout(raw: unknown): Layout {
  const main = readCore(raw);
  if (main === null) return defaultLayout();
  const before = isRecord(raw) ? readCore(raw.beforeFocus) : null;
  return { ...main, beforeFocus: before };
}

/** The stored layout, or the default when there is none, it does not parse, or storage throws. */
export function loadLayout(storage: Pick<Storage, "getItem"> | undefined = safeStorage()): Layout {
  try {
    const text = storage?.getItem(STORAGE_KEY);
    return text ? parseLayout(JSON.parse(text)) : defaultLayout();
  } catch {
    return defaultLayout();
  }
}

/** Keep it. A storage that refuses (a full quota, a locked-down webview) costs the next launch its
 *  layout and nothing else, so the failure is swallowed rather than reaching the screen. */
export function saveLayout(layout: Layout, storage: Pick<Storage, "setItem"> | undefined = safeStorage()): void {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // the screen works without it
  }
}

/** The person's own layout, saved on request and applied from the command palette (phase 5). */
export const MINE_KEY = "chimera.layout.v1.code.mine";

export function saveMine(layout: Layout, storage: Pick<Storage, "setItem"> | undefined = safeStorage()): boolean {
  try {
    // Saved without focus mode's memory: "mine" is a layout, not a layout plus the one it replaced.
    storage?.setItem(MINE_KEY, JSON.stringify({ ...layout, beforeFocus: null }));
    return storage !== undefined;
  } catch {
    return false;
  }
}

/** The saved layout, parsed like any stored one, or null when there is none. */
export function loadMine(storage: Pick<Storage, "getItem"> | undefined = safeStorage()): Layout | null {
  try {
    const text = storage?.getItem(MINE_KEY);
    return text ? parseLayout(JSON.parse(text)) : null;
  } catch {
    return null;
  }
}

function safeStorage(): Storage | undefined {
  try {
    return typeof window === "undefined" ? undefined : window.localStorage;
  } catch {
    return undefined;
  }
}
