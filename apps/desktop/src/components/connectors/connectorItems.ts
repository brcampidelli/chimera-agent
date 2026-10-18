/**
 * Connectors in the catalog's neutral shape (study 04, §1.2 and §6, PR 6): one adapter each for a
 * configured MCP server, an OpenAPI connector, an entry of the curated MCP catalogue and an entry of
 * the public connectors catalogue, and the sections each tab draws. The screen never learns which
 * of the four it is drawing; what each one asks of the owner stays in its own components.
 */
import type { CatalogSectionSpec } from "@/components/catalog/CatalogScreen";
import type { CapabilityFacts, CatalogItem, CatalogStatus, OfficialProof } from "@/components/catalog/model";
import { entryText } from "@/components/Mcp";
import { licenseOf } from "@/components/plugins/pluginItems";
import type { McpCatalogEntry } from "@/lib/api";
import type { TFunc } from "@/lib/i18n";
import type { Connector, McpServer, PublicCatalog, PublicCatalogItem } from "@/lib/types";

/** Category ids of the two kinds the owner adds by hand. The public catalogue brings its own. */
export const CATEGORY_MCP = "mcp";
export const CATEGORY_OPENAPI = "openapi";

export const mcpItemId = (name: string) => `connector:mcp:${name}`;
export const openapiItemId = (name: string) => `connector:openapi:${name}`;
export const curatedItemId = (id: string) => `connector:catalog:${id}`;
export const publicItemId = (id: string) => `connector:public:${id}`;

const NO_PROOF: OfficialProof = { official: false, vendor_org: null, repo_owner: null, from_anthropic: false, library: false };

const UNKNOWN: CapabilityFacts = { textOnly: false, servers: 0, network: false, inert: 0, unknown: true };

function base(id: string, name: string): Omit<CatalogItem, "description" | "capabilities" | "status" | "category"> {
  return {
    id,
    kind: "connector",
    name,
    author: null,
    source: null,
    license: null,
    proof: NO_PROOF,
    stars: null,
    stars_at: null,
    added_at: null,
    updated_at: null,
    installable: false,
    refusal: null,
  };
}

/** The line a configured server is known by: its remote host, or the command it starts. */
export function serverCommand(server: McpServer): string {
  return server.url_host ?? [server.command, ...server.args].join(" ");
}

/**
 * Who has to act first. A held server (its tools changed since the owner approved them) and one
 * that still needs a browser sign-in wait for the owner; a paused one is off; the rest are on.
 */
export function serverStatus(server: McpServer): CatalogStatus {
  if (server.manifest_held) return "held";
  if (server.disabled) return "inactive";
  if (server.oauth && !server.oauth.connected) return "pending";
  return "active";
}

export function fromServer(server: McpServer): CatalogItem {
  const remote = !!server.url_host;
  return {
    ...base(mcpItemId(server.name), server.name),
    description: serverCommand(server),
    category: CATEGORY_MCP,
    keywords: [server.command, ...server.args, ...server.env_keys],
    // A remote server starts nothing here and opens a connection; a command starts one process and
    // what it reaches from there is not something this list knows.
    capabilities: { textOnly: false, servers: remote ? 0 : 1, network: remote, inert: 0, unknown: false },
    status: serverStatus(server),
  };
}

/** An OpenAPI connector arrives switched off and waits for the owner: "off — pending". */
export function fromConnector(connector: Connector, t: TFunc): CatalogItem {
  return {
    ...base(openapiItemId(connector.name), connector.name),
    description: t("connectors.reaches", { url: connector.base_url }),
    category: CATEGORY_OPENAPI,
    keywords: [connector.base_url, connector.source],
    capabilities: { textOnly: false, servers: 0, network: true, inert: 0, unknown: false },
    status: connector.enabled ? "active" : "pending",
  };
}

/**
 * The proof the "official vendor" badge reads (§3.2): the catalogue's `official` flag, the owner of
 * the repository the entry names, and the vendor's organisations. The shell's `badgeOf` compares
 * the last two, so a flag with no matching owner earns nothing.
 */
export function curatedProof(entry: McpCatalogEntry): OfficialProof {
  const owner = (entry.repo ?? "").split("/")[0]?.trim() || null;
  const orgs = entry.vendor_orgs ?? [];
  const vendor = orgs.find((o) => owner !== null && o.toLowerCase() === owner.toLowerCase()) ?? orgs[0] ?? null;
  return { official: entry.official, vendor_org: vendor, repo_owner: owner, from_anthropic: false, library: false };
}

export function fromCuratedEntry(entry: McpCatalogEntry, configured: McpServer | undefined, t: TFunc): CatalogItem {
  const { summary, containment } = entryText(entry, t);
  return {
    ...base(curatedItemId(entry.id), entry.label),
    description: summary,
    source: entry.repo || null,
    category: CATEGORY_MCP,
    keywords: [entry.id, entry.command, entry.runner, ...entry.args],
    // Every curated entry is a stdio launch; one that names an https:// address goes through a
    // bridge to a remote server, which is a connection the owner should see on the card.
    capabilities: {
      textOnly: false,
      servers: 1,
      network: entry.args.some((a) => /^https?:\/\//.test(a)),
      inert: 0,
      unknown: false,
    },
    limit: containment,
    status: configured ? serverStatus(configured) : null,
    proof: curatedProof(entry),
    installable: entry.available,
    refusal: entry.available ? null : t("mcp.catalog.needs", { n: entry.runner }),
  };
}

/**
 * An entry of the public catalogue. The listing carries no address, command or secret, so nothing
 * here can be added from the card: it says so, and the detail shows where to read the entry. What it
 * would run is unknown, and drawn as unknown — not as "instructions only".
 */
export function fromPublicItem(item: PublicCatalogItem, t: TFunc): CatalogItem {
  const sha = item.source.sha.slice(0, 7);
  return {
    ...base(publicItemId(item.id), item.name),
    description: item.description,
    author: item.author || null,
    source: `${item.source.owner}/${item.source.repo}@${sha}`,
    license: licenseOf(item.license),
    category: item.category,
    keywords: item.categories_secondary ?? [],
    capabilities: UNKNOWN,
    status: null,
    stars: item.stars ?? null,
    stars_at: item.stars_at ?? null,
    added_at: item.added_at ?? null,
    updated_at: item.updated_at ?? null,
    installable: false,
    refusal: item.refusal || t("connectorsUi.public.byHand"),
  };
}

/** Mine, grouped by who has to act (§2.5): what waits for the owner, then what is on, then off. */
export function mineSections(
  servers: readonly McpServer[],
  connectors: readonly Connector[],
  t: TFunc,
): CatalogSectionSpec[] {
  const items = [...servers.map(fromServer), ...connectors.map((c) => fromConnector(c, t))];
  const of = (states: readonly CatalogStatus[]) => items.filter((i) => i.status !== null && states.includes(i.status));
  return [
    { id: "waiting", title: t("connectorsUi.mine.waiting"), items: of(["pending", "held"]) },
    { id: "active", title: t("connectorsUi.mine.active"), items: of(["active"]) },
    { id: "off", title: t("connectorsUi.mine.off"), items: of(["inactive", "denied"]) },
  ];
}

/**
 * Discover: the curated MCP catalogue first — each entry checked against its own documentation, with
 * what limits it on the card — then the public connectors catalogue. No "popular": nothing on this
 * machine counts installs.
 */
export function discoverSections(
  entries: readonly McpCatalogEntry[],
  servers: readonly McpServer[],
  publicCatalog: PublicCatalog | undefined,
  t: TFunc,
): CatalogSectionSpec[] {
  const byName = new Map(servers.map((s) => [s.name, s]));
  return [
    {
      id: "curated",
      title: t("connectorsUi.discover.curated"),
      items: entries.map((e) => fromCuratedEntry(e, byName.get(e.id), t)),
    },
    {
      id: "public",
      title: t("connectorsUi.discover.public"),
      items: (publicCatalog?.items ?? []).map((i) => fromPublicItem(i, t)),
    },
  ];
}
