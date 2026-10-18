/**
 * The neutral shape a catalog screen draws, whatever it lists.
 *
 * Skills, connectors and plugins each arrive from their own route with their own fields; an adapter
 * per kind (later PRs) fills this in, and the screen never learns which kind it is drawing. What is
 * deliberately NOT here: any install, download or user count. Nothing on this machine measures one,
 * and a number invented on the screen where someone decides what to trust is the worst place to
 * invent it. The model test fails if such a field appears.
 */
import type { CapabilityFacts } from "@/components/plugins/capabilities";

export type { CapabilityFacts };

export type CatalogKind = "skill" | "connector" | "plugin";

/** Installed states. An item that is not installed has no status (`null`). */
export type CatalogStatus = "pending" | "active" | "inactive" | "held" | "denied" | "learned";

/** The facts a badge may rest on. Each one is checkable; none is an opinion about the code. */
export interface OfficialProof {
  /** The curated catalog says the service itself publishes it. */
  official: boolean;
  /** The organisation that owns the service ("supabase"), as the curated catalog records it. */
  vendor_org: string | null;
  /** The owner of the repository the item is pinned to, read from its source. */
  repo_owner: string | null;
  /** From an `anthropics/*` marketplace, as the backend already decides. */
  from_anthropic: boolean;
  /** Text shipped inside the app and reviewed in its repository. */
  library: boolean;
}

export interface CatalogItem {
  /** Stable across kinds: "plugin:pdf@market", "connector:github", "skill:pdf". */
  id: string;
  kind: CatalogKind;
  name: string;
  description: string;
  author: string | null;
  /** Where it comes from, short enough for one line: "anthropics/claude-plugins@3f2a91c". */
  source?: string | null;
  /** `null` = we could not read it, which is a warning, not "no licence". */
  license: { spdx: string; permissive: boolean } | null;
  /** No licence applies at all — the owner's own text, distilled from their runs. The chip is left
   *  out rather than warning about terms that do not exist. Omit for anything someone else wrote. */
  noLicense?: boolean;
  /** A category id; the screen asks its caller how to word it. */
  category: string;
  keywords?: readonly string[];
  capabilities: CapabilityFacts;
  status: CatalogStatus | null;
  proof: OfficialProof;
  /** GitHub stars, only when actually fetched — never estimated. `null` = no data, not zero. */
  stars: number | null;
  /** When `stars` was read (ISO date). Shown beside the number so it is never read as live. */
  stars_at: string | null;
  added_at: string | null;
  updated_at: string | null;
  installable: boolean;
  /** Why it cannot be installed, shown on the card. Hiding the item would make "lists twelve" read
   *  as "lists eight". */
  refusal: string | null;
}

export type CatalogBadge = "vendor" | "anthropic" | "chimera";

/**
 * The one badge an item earns, by rule. "Official vendor" needs BOTH the curated flag and the
 * pinned repository being owned by the vendor's own organisation: the flag alone is a claim, the
 * owner is the check. There is no "verified" badge — nobody audits the code, and a badge saying so
 * would say the opposite of what is true.
 */
export function badgeOf(item: Pick<CatalogItem, "proof">): CatalogBadge | null {
  const p = item.proof;
  if (
    p.official &&
    p.vendor_org &&
    p.repo_owner &&
    p.vendor_org.trim().toLowerCase() === p.repo_owner.trim().toLowerCase()
  )
    return "vendor";
  if (p.from_anthropic) return "anthropic";
  if (p.library) return "chimera";
  return null;
}

// ── Filters ───────────────────────────────────────────────────────────────────────────────────────

export const RUNS = ["text", "process", "network", "inert", "unknown"] as const;
export type RunsValue = (typeof RUNS)[number];
export const LICENSES = ["permissive", "other", "unread"] as const;
export type LicenseValue = (typeof LICENSES)[number];
export const STATES = ["none", "pending", "active", "held", "inactive"] as const;
export type StateValue = (typeof STATES)[number];
export const SORTS = ["relevance", "name", "recent", "stars"] as const;
export type SortValue = (typeof SORTS)[number];
export type CatalogView = "mine" | "discover";

export interface CatalogFilters {
  cat: readonly string[];
  runs: readonly RunsValue[];
  license: readonly LicenseValue[];
  state: readonly StateValue[];
}

export const NO_FILTERS: CatalogFilters = { cat: [], runs: [], license: [], state: [] };

export type FilterGroup = keyof CatalogFilters;
export const FILTER_GROUPS: readonly FilterGroup[] = ["cat", "runs", "license", "state"];

function list<T extends string>(raw: string | null, allowed?: readonly T[]): T[] {
  const parts = (raw ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const kept = allowed ? parts.filter((p): p is T => (allowed as readonly string[]).includes(p)) : (parts as T[]);
  return [...new Set(kept)];
}

/** Read the filters out of the URL. An unknown value is dropped, never an empty screen. */
export function parseFilters(params: URLSearchParams): CatalogFilters {
  return {
    cat: list(params.get("cat")),
    runs: list(params.get("runs"), RUNS),
    license: list(params.get("license"), LICENSES),
    state: list(params.get("state"), STATES),
  };
}

export function parseView(params: URLSearchParams): CatalogView {
  return params.get("view") === "discover" ? "discover" : "mine";
}

/** `null` when the URL names no sort (or one that does not exist): the screen picks the default. */
export function parseSort(params: URLSearchParams): SortValue | null {
  const raw = params.get("sort");
  return raw && (SORTS as readonly string[]).includes(raw) ? (raw as SortValue) : null;
}

export function filterCount(f: CatalogFilters): number {
  return FILTER_GROUPS.reduce((n, g) => n + f[g].length, 0);
}

function runsValues(facts: CapabilityFacts): RunsValue[] {
  // Unknown answers to "unknown" and to nothing else: a package we have not downloaded is not
  // "instructions only" just because we counted zero servers in it.
  if (facts.unknown) return ["unknown"];
  const out: RunsValue[] = [];
  if (facts.textOnly) out.push("text");
  if (facts.servers > 0) out.push("process");
  if (facts.network) out.push("network");
  if (facts.inert > 0) out.push("inert");
  return out;
}

function licenseValue(item: CatalogItem): LicenseValue {
  if (!item.license) return "unread";
  return item.license.permissive ? "permissive" : "other";
}

function stateValue(item: CatalogItem): StateValue | null {
  if (item.status === null) return "none";
  if (item.status === "pending" || item.status === "active" || item.status === "held" || item.status === "inactive")
    return item.status;
  return null; // denied and learned answer to no state filter
}

/** Within a group the values are alternatives (OR); across groups every group must hold (AND). */
export function matchesFilters(item: CatalogItem, f: CatalogFilters): boolean {
  if (f.cat.length && !f.cat.includes(item.category)) return false;
  if (f.runs.length && !runsValues(item.capabilities).some((v) => f.runs.includes(v))) return false;
  if (f.license.length && !f.license.includes(licenseValue(item))) return false;
  if (f.state.length) {
    const s = stateValue(item);
    if (s === null || !f.state.includes(s)) return false;
  }
  return true;
}

// ── Search and sort ───────────────────────────────────────────────────────────────────────────────

/** Lower case, accents gone: "Análise" is found by "analise". */
export function normalize(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().trim();
}

/** Lower is better; `null` = does not match. Name beats author and keywords, which beat prose. */
export function relevance(item: CatalogItem, query: string): number | null {
  const q = normalize(query);
  if (!q) return 0;
  const name = normalize(item.name);
  if (name.startsWith(q)) return 0;
  if (name.includes(q)) return 1;
  const near = [item.author ?? "", item.category, ...(item.keywords ?? [])].map(normalize);
  if (near.some((s) => s.includes(q))) return 2;
  if (normalize(item.description).includes(q)) return 3;
  return null;
}

export interface Sorted {
  /** Items that have the value the sort reads, in order. */
  ranked: CatalogItem[];
  /** Items without it (no stars fetched, no date known): after the ranked ones, never as zero. */
  unranked: CatalogItem[];
}

function dateOf(item: CatalogItem): number | null {
  const raw = item.updated_at ?? item.added_at;
  if (!raw) return null;
  const t = Date.parse(raw);
  return Number.isNaN(t) ? null : t;
}

export function sortItems(items: readonly CatalogItem[], sort: SortValue, lang: string, query = ""): Sorted {
  const collator = new Intl.Collator(lang, { sensitivity: "base" });
  const byName = (a: CatalogItem, b: CatalogItem) => collator.compare(a.name, b.name);
  if (sort === "stars") {
    const ranked = items.filter((i) => i.stars !== null);
    const unranked = items.filter((i) => i.stars === null);
    ranked.sort((a, b) => (b.stars ?? 0) - (a.stars ?? 0) || byName(a, b));
    return { ranked, unranked: unranked.sort(byName) };
  }
  if (sort === "recent") {
    const ranked = items.filter((i) => dateOf(i) !== null);
    const unranked = items.filter((i) => dateOf(i) === null);
    ranked.sort((a, b) => (dateOf(b) ?? 0) - (dateOf(a) ?? 0) || byName(a, b));
    return { ranked, unranked: unranked.sort(byName) };
  }
  if (sort === "relevance" && normalize(query)) {
    const score = (i: CatalogItem) => relevance(i, query) ?? 99;
    return { ranked: [...items].sort((a, b) => score(a) - score(b) || byName(a, b)), unranked: [] };
  }
  return { ranked: [...items].sort(byName), unranked: [] };
}

/** Which sorts can be offered: "recent" and "stars" only when some item carries the data. */
export function availableSorts(items: readonly CatalogItem[], searching: boolean): SortValue[] {
  const out: SortValue[] = [];
  if (searching) out.push("relevance");
  out.push("name");
  if (items.some((i) => dateOf(i) !== null)) out.push("recent");
  if (items.some((i) => i.stars !== null)) out.push("stars");
  return out;
}
