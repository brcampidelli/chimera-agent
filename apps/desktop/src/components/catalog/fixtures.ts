import type { CatalogItem } from "@/components/catalog/model";

/** For tests: a complete item with every optional fact absent; each test overrides what it is about. */
export function item(over: Partial<CatalogItem> = {}): CatalogItem {
  return {
    id: `plugin:${over.name ?? "thing"}`,
    kind: "plugin",
    name: "thing",
    description: "Does a thing.",
    author: null,
    license: { spdx: "MIT", permissive: true },
    category: "dev",
    capabilities: { textOnly: true, servers: 0, network: false, inert: 0, unknown: false },
    status: null,
    proof: { official: false, vendor_org: null, repo_owner: null, from_anthropic: false, library: false },
    stars: null,
    stars_at: null,
    added_at: null,
    updated_at: null,
    installable: true,
    refusal: null,
    ...over,
  };
}

export const UNKNOWN = { textOnly: false, servers: 0, network: false, inert: 0, unknown: true };
export const SERVER = { textOnly: false, servers: 1, network: true, inert: 0, unknown: false };
