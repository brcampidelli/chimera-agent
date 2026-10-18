/**
 * Skills in the catalog's neutral shape (study 04, §1.2): one adapter per source a skill comes
 * from, and the sections each tab draws. The screen never learns it is drawing a skill; the
 * governance of one — approving a learned card, switching an installed one on — stays in the
 * skill's own components.
 *
 * Five sources, each with its own id prefix so two copies of one name never collapse into one card:
 * a card the agent LEARNED from the owner's runs, a bundle INSTALLED on this machine, a card from
 * the LIBRARY that ships in the box, an entry of the third-party CATALOG, and an item of the PINNED
 * public catalogue. The pinned catalogue does not replace the library copy of a name it shares:
 * one is text reviewed in this repository and shipped, the other is read from a commit of another
 * repository, and nothing on this machine checks that they say the same thing. Both are shown, each
 * with its source on the card.
 */
import type { CatalogSectionSpec } from "@/components/catalog/CatalogScreen";
import type { CapabilityFacts, CatalogItem, CatalogStatus } from "@/components/catalog/model";
import { shortSha } from "@/components/SkillsActiveNow";
import type { TFunc } from "@/lib/i18n";
import type { CatalogEntry, LibraryCard, PinnedCatalog, PinnedCatalogItem, SkillBundle, SkillStat } from "@/lib/types";

export const skillId = {
  learned: (name: string) => `skill:learned:${name}`,
  bundle: (name: string) => `skill:bundle:${name}`,
  library: (name: string) => `skill:library:${name}`,
  catalog: (name: string) => `skill:catalog:${name}`,
  pinned: (id: string) => `skill:pinned:${id}`,
};

/** Categories a skill gets from where it came rather than from a catalogue's topic. */
export const LEARNED_CATEGORY = "learned";
export const UPLOAD_CATEGORY = "upload";
const OTHER = "other";

/** The ratings in the order a person reads them: works, then the caveats from cheapest to fix. */
export const RATINGS = [
  "native",
  "needs_setup",
  "needs_service",
  "needs_heavy",
  "os_locked",
  "needs_adaptation",
] as const;
export type Rating = (typeof RATINGS)[number];

/** How a rating reads at a glance. Only `native` is good news; the rest are caveats, and a caveat
 *  rendered in the same colour as a pass is a caveat nobody sees. */
export const TONES: Record<string, "ok" | "accent" | "warn" | "muted"> = {
  native: "ok",
  needs_setup: "accent",
  needs_service: "warn",
  needs_heavy: "warn",
  os_locked: "warn",
  needs_adaptation: "muted",
};

/** Reading order through a piece of work. Alphabetical would put "verify" before "build". */
export const STAGES = ["define", "build", "verify", "review", "ship"] as const;

/** How many cards a Discover section draws before "Show all" (§2.4). */
const SECTION_LIMIT = 6;

/** SPDX ids read as permissive, as `pluginItems` reads them. */
const PERMISSIVE = new Set(["MIT", "APACHE-2.0", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "ISC", "0BSD", "UNLICENSE", "CC0-1.0"]);

const TEXT_ONLY: CapabilityFacts = { textOnly: true, servers: 0, network: false, inert: 0, unknown: false };
/** Not downloaded, so what is in the folder besides the text is not known yet. */
const UNKNOWN: CapabilityFacts = { textOnly: false, servers: 0, network: false, inert: 0, unknown: true };
/** On disk with more than markdown in it. The card's own note says how many files and why they
 *  matter; the shared line has no word for "scripts its text may ask the agent to run". */
const WITH_FILES: CapabilityFacts = { textOnly: false, servers: 0, network: false, inert: 0, unknown: false };

export function ratingOf(entry: Pick<CatalogEntry, "portability">): Rating {
  const raw = entry.portability || "native";
  return (RATINGS as readonly string[]).includes(raw) ? (raw as Rating) : "needs_adaptation";
}

/** Files in an installed folder that are not text the prompt reads. */
export function otherFiles(bundle: Pick<SkillBundle, "files">): number {
  return (bundle.files ?? []).filter((f) => !/\.md$/i.test(f)).length;
}

function factsOfBundle(bundle: SkillBundle): CapabilityFacts {
  if (!bundle.files || bundle.files.length === 0) return UNKNOWN;
  return otherFiles(bundle) === 0 ? TEXT_ONLY : WITH_FILES;
}

export function isPermissive(spdx: string | null | undefined): boolean {
  return PERMISSIVE.has((spdx ?? "").trim().toUpperCase());
}

function licenseOf(spdx: string | null | undefined, permissive?: boolean): CatalogItem["license"] {
  const value = (spdx ?? "").trim();
  if (!value) return null;
  return { spdx: value, permissive: permissive ?? isPermissive(value) };
}

function bundleStatus(raw: string | null | undefined): CatalogStatus | null {
  if (!raw) return null;
  if (raw === "active" || raw === "inactive" || raw === "pending") return raw;
  return "pending";
}

function base(id: string, name: string): CatalogItem {
  return {
    id,
    kind: "skill",
    name,
    description: "",
    author: null,
    source: null,
    license: null,
    category: OTHER,
    keywords: [],
    capabilities: TEXT_ONLY,
    status: null,
    proof: { official: false, vendor_org: null, repo_owner: null, from_anthropic: false, library: false },
    stars: null,
    stars_at: null,
    added_at: null,
    updated_at: null,
    installable: false,
    refusal: null,
  };
}

export function fromLearned(stat: SkillStat, t: TFunc): CatalogItem {
  const rate = stat.rate != null ? ` · ${Math.round(stat.rate * 100)}%` : "";
  return {
    ...base(skillId.learned(stat.name), stat.name),
    description: t("skills.stats", { uses: stat.uses, wins: stat.successes }) + rate,
    category: LEARNED_CATEGORY,
    status: "learned",
    // Distilled from the owner's own runs: there are no terms to read, and "licence not read" in
    // amber would say there were.
    noLicense: true,
  };
}

/** "github.com/x/y/tree/main/skills/maps" without the scheme: one line, readable at a glance. */
function shortUrl(url: string | null | undefined): string {
  return (url ?? "").replace(/^https?:\/\//, "").replace(/^github\.com\//, "");
}

export function fromBundle(bundle: SkillBundle, entry?: CatalogEntry): CatalogItem {
  const name = bundle.name ?? "";
  const upload = bundle.origin === "upload";
  const pin = /^[0-9a-f]{40}$/i.test(bundle.ref ?? "") ? `@${shortSha(bundle.ref)}` : "";
  return {
    ...base(skillId.bundle(name), name),
    description: bundle.description ?? entry?.description ?? "",
    author: entry?.author || null,
    source: upload ? bundle.source || null : `${shortUrl(bundle.source || entry?.homepage)}${pin}` || null,
    license: licenseOf(bundle.license || entry?.license, entry?.license ? entry.permissive : undefined),
    category: entry?.topic || (upload ? UPLOAD_CATEGORY : bundle.origin || UPLOAD_CATEGORY),
    capabilities: factsOfBundle(bundle),
    status: bundleStatus(bundle.status || "pending"),
  };
}

export function fromLibrary(card: LibraryCard): CatalogItem {
  return {
    ...base(skillId.library(card.name), card.name),
    description: card.description,
    license: licenseOf(card.license),
    category: card.topic || OTHER,
    keywords: card.triggers,
    // Imported, it lives in the learned store, which is that store's own word for it.
    status: card.imported ? "learned" : null,
    proof: { official: false, vendor_org: null, repo_owner: null, from_anthropic: false, library: true },
    installable: !card.imported,
  };
}

export function fromEntry(entry: CatalogEntry, bundle?: SkillBundle): CatalogItem {
  const name = entry.name ?? "";
  return {
    ...base(skillId.catalog(name), name),
    description: entry.description ?? "",
    author: entry.author || null,
    // The commit the install resolved to, when there is one: which bytes are on disk.
    source: `${shortUrl(entry.homepage)}${bundle && /^[0-9a-f]{40}$/i.test(bundle.ref ?? "") ? `@${shortSha(bundle.ref)}` : ""}` || null,
    license: licenseOf(entry.license, entry.permissive),
    category: entry.topic || OTHER,
    keywords: entry.requires ?? [],
    capabilities: bundle ? factsOfBundle(bundle) : UNKNOWN,
    status: bundleStatus(entry.installed),
    installable: true,
  };
}

export function fromPinned(item: PinnedCatalogItem, t: TFunc): CatalogItem {
  const s = item.source;
  return {
    ...base(skillId.pinned(item.id), item.name),
    description: item.description ?? "",
    author: item.author || null,
    source: `${s.owner}/${s.repo}@${s.sha.slice(0, 7)}`,
    license: licenseOf(item.license),
    category: item.category || OTHER,
    keywords: item.categories_secondary ?? [],
    capabilities: UNKNOWN,
    stars: item.stars ?? null,
    stars_at: item.stars_at ?? null,
    added_at: item.added_at ?? null,
    updated_at: item.updated_at ?? null,
    // Reading this catalogue installs nothing, and no route here installs from it yet. Said on the
    // card rather than offering a "+" that would do nothing — or the server's own reason, when it
    // has one.
    installable: false,
    refusal: item.refusal || t("skills.pinned.readOnly"),
  };
}

export interface SkillSources {
  stats: readonly SkillStat[];
  bundles: readonly SkillBundle[];
  library: readonly LibraryCard[];
  entries: readonly CatalogEntry[];
  pinned: PinnedCatalog | undefined;
}

/**
 * Mine, grouped by who has to act (§2.5): what waits for the owner first — an installed skill not
 * read yet, a learned card held for approval — then what is on, then what is off, then the cards the
 * agent learned.
 */
export function mineSections(src: SkillSources, t: TFunc): CatalogSectionSpec[] {
  const byName = new Map(src.entries.map((e) => [e.name ?? "", e] as const));
  const bundles = src.bundles.map((b) => fromBundle(b, byName.get(b.name ?? "")));
  const pendingLearned = src.stats.filter((s) => s.status === "pending").map((s) => fromLearned(s, t));
  const learned = src.stats.filter((s) => s.status !== "pending").map((s) => fromLearned(s, t));
  const of = (status: CatalogStatus) => bundles.filter((i) => i.status === status);
  return [
    { id: "waiting", title: t("skills.mine.waiting"), items: [...of("pending"), ...pendingLearned] },
    { id: "active", title: t("skills.mine.active"), items: of("active") },
    { id: "off", title: t("skills.mine.off"), items: of("inactive") },
    { id: "learned", title: t("skills.learned"), items: learned },
  ];
}

/** The library in the order of the work, then by name; a card with no stage last. */
function byStage(cards: readonly LibraryCard[]): LibraryCard[] {
  const rank = (c: LibraryCard) => {
    const i = (STAGES as readonly string[]).indexOf(c.stage);
    return i === -1 ? STAGES.length : i;
  };
  return [...cards].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

export type Grouping = "topic" | "name";

/**
 * Discover, in sections a person can check (§2.4): the library that ships in the box, the pinned
 * public catalogue, then the third-party catalogue by topic — or in one list when the person asks
 * for it by name. A portability filter narrows to what has that rating; the library is text in this
 * agent's own format, so it rates as working here, and the pinned catalogue carries no rating, so a
 * rating filter leaves it out rather than guessing one.
 */
export function discoverSections(
  src: SkillSources,
  opts: { grouping: Grouping; fit: Rating | null },
  t: TFunc,
): CatalogSectionSpec[] {
  const bundleByName = new Map(src.bundles.map((b) => [b.name ?? "", b] as const));
  const fit = opts.fit;
  const library = fit === null || fit === "native" ? byStage(src.library).map(fromLibrary) : [];
  const pinned = fit === null ? (src.pinned?.items ?? []).map((i) => fromPinned(i, t)) : [];
  const entries = src.entries
    .filter((e) => fit === null || ratingOf(e) === fit)
    .map((e) => fromEntry(e, e.installed ? bundleByName.get(e.name ?? "") : undefined));

  const sections: CatalogSectionSpec[] = [
    { id: "chimera", title: t("skills.discover.library"), items: library, limit: SECTION_LIMIT, ordered: true },
    { id: "pinned", title: t("skills.discover.pinned"), items: pinned, limit: SECTION_LIMIT },
  ];
  if (opts.grouping === "name") {
    sections.push({ id: "catalog", title: t("catalog.title"), items: entries });
    return sections;
  }
  const topics = new Map<string, CatalogItem[]>();
  for (const item of entries) topics.set(item.category, [...(topics.get(item.category) ?? []), item]);
  for (const [topic, items] of [...topics.entries()].sort(([a], [b]) => a.localeCompare(b)))
    sections.push({ id: `topic-${topic}`, title: topic, items });
  return sections;
}
