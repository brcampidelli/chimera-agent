import {
  CARD_KINDS,
  PANEL_IDS,
  PANELS,
  SIZE_LIMITS,
  defaultLayout,
  type CardKind,
  type Layout,
  type LayoutCore,
  type Mode,
  type PanelId,
  type Region,
  type Zone,
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

function readCore(value: unknown): LayoutCore | null {
  if (!isRecord(value) || value.version !== 1) return null;
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
    }
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

  return { version: 1, regions, panels, cards, maximized };
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
