/**
 * Plugins in the catalog's neutral shape (study 04, §1.2): one adapter for an installed plugin, one
 * for a marketplace entry, and the sections each tab draws. The screen never learns it is drawing a
 * plugin; the governance of one (the switch, the review) stays in the plugin's own components.
 */
import type { CatalogSectionSpec } from "@/components/catalog/CatalogScreen";
import type { CatalogItem, CatalogStatus } from "@/components/catalog/model";
import type { TFunc } from "@/lib/i18n";
import type { Plugin, PluginMarketplace, PluginMarketplaceEntry } from "@/lib/types";
import { factsOfCounts, factsOfPlugin, shortPin } from "@/components/plugins/capabilities";

/** The category id of an entry whose marketplace names none. Worded by the screen, never hidden. */
export const NO_CATEGORY = "none";

/** How many cards "Work without setup" draws before "Show all". */
const NO_SETUP_LIMIT = 6;

/** SPDX ids read as permissive. Anything else — including a licence we could not read — is a
 *  warning chip: these are downloads, and the terms belong where the decision is made. */
const PERMISSIVE = new Set(["MIT", "APACHE-2.0", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "ISC", "0BSD", "UNLICENSE", "CC0-1.0"]);

export function pluginItemId(name: string): string {
  return `plugin:${name}`;
}

export function entryItemId(market: string, name: string): string {
  return `plugin:${name}@${market}`;
}

export function licenseOf(raw: string | null | undefined): CatalogItem["license"] {
  const spdx = (raw ?? "").trim();
  if (!spdx) return null;
  return { spdx, permissive: PERMISSIVE.has(spdx.toUpperCase()) };
}

const STATUSES: readonly CatalogStatus[] = ["pending", "active", "inactive", "held"];

function statusOf(raw: string | null | undefined, denied = false): CatalogStatus | null {
  if (denied) return "denied";
  if (!raw) return null;
  return (STATUSES as readonly string[]).includes(raw) ? (raw as CatalogStatus) : "pending";
}

/** "someone/my-plugin@a1b2c3d": the source with its pin cut to the seven characters a person
 *  compares at a glance. */
export function shortSource(plugin: Pick<Plugin, "source" | "ref" | "tree_sha256">): string {
  const base = (plugin.source ?? "").replace(/^github:/, "").replace(/@[0-9a-f]{7,}$/i, "");
  const pin = shortPin(plugin.ref || plugin.tree_sha256);
  return pin ? `${base}@${pin}` : base;
}

export function fromPlugin(plugin: Plugin): CatalogItem {
  return {
    id: pluginItemId(plugin.name),
    kind: "plugin",
    name: plugin.display_name || plugin.name,
    description: plugin.description ?? "",
    author: plugin.author || null,
    source: shortSource(plugin),
    license: licenseOf(plugin.license),
    category: NO_CATEGORY,
    keywords: plugin.keywords ?? [],
    capabilities: factsOfPlugin(plugin),
    status: statusOf(plugin.status ?? "pending", plugin.denied),
    proof: { official: false, vendor_org: null, repo_owner: null, from_anthropic: !!plugin.from_anthropic, library: false },
    stars: null,
    stars_at: null,
    added_at: null,
    updated_at: null,
    installable: false,
    refusal: null,
  };
}

function entrySource(entry: PluginMarketplaceEntry): string {
  const s = entry.source;
  if (!s) return "";
  if (s.repo) return s.sha ? `${s.repo}@${s.sha.slice(0, 7)}` : s.repo;
  return s.url || s.path || "";
}

export function fromMarketEntry(market: PluginMarketplace | undefined, marketName: string, entry: PluginMarketplaceEntry, t: TFunc): CatalogItem {
  return {
    id: entryItemId(marketName, entry.name),
    kind: "plugin",
    name: entry.name,
    description: entry.description ?? "",
    author: null,
    source: entrySource(entry),
    // A listing does not carry the licence; the package is not downloaded to answer it.
    license: null,
    category: entry.category || NO_CATEGORY,
    keywords: entry.tags ?? [],
    capabilities: factsOfCounts(entry.capabilities),
    status: statusOf(entry.installed),
    proof: {
      official: false,
      vendor_org: null,
      repo_owner: null,
      from_anthropic: (market?.repo ?? "").toLowerCase().startsWith("anthropics/"),
      library: false,
    },
    stars: null,
    stars_at: null,
    added_at: null,
    updated_at: null,
    installable: entry.installable,
    refusal: entry.installable ? null : t("plugins.notInstallable", { reason: entry.refusal || "—" }),
  };
}

/**
 * Mine, grouped by who has to act (§2.5): what waits for the owner first — pending and held — then
 * what is on, then what is off or refused by policy.
 */
export function mineSections(plugins: readonly Plugin[], t: TFunc): CatalogSectionSpec[] {
  const items = plugins.map(fromPlugin);
  const of = (states: readonly CatalogStatus[]) => items.filter((i) => i.status !== null && states.includes(i.status));
  return [
    { id: "waiting", title: t("plugins.mine.waiting"), items: of(["pending", "held"]) },
    { id: "active", title: t("plugins.mine.active"), items: of(["active"]) },
    { id: "off", title: t("plugins.mine.off"), items: of(["inactive", "denied"]) },
  ];
}

export interface EntryRow {
  market: string;
  entry: PluginMarketplaceEntry;
  item: CatalogItem;
}

/**
 * Discover, in sections a person can check (§2.4). There is no "popular" or "trending": nothing on
 * this machine counts installs. "Work without setup" is what is instructions only and installable;
 * then each marketplace, so where a plugin comes from stays visible. The shell draws each item once,
 * in this order.
 */
export function discoverSections(rows: readonly EntryRow[], markets: readonly string[], t: TFunc): CatalogSectionSpec[] {
  const easy = rows
    .filter((r) => r.item.capabilities.textOnly && r.item.installable && r.item.status === null)
    .map((r) => r.item);
  return [
    { id: "no-setup", title: t("plugins.discover.noSetup"), items: easy, limit: NO_SETUP_LIMIT },
    ...markets.map((name) => ({
      id: `market-${name}`,
      title: t("plugins.discover.from", { name }),
      items: rows.filter((r) => r.market === name).map((r) => r.item),
    })),
  ];
}
