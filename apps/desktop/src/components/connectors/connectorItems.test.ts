import { describe, expect, it } from "vitest";

import { badgeOf } from "@/components/catalog/model";
import { curatedProof, serverStatus } from "@/components/connectors/connectorItems";
import type { McpCatalogEntry } from "@/lib/api";
import type { McpServer } from "@/lib/types";

const ENTRY: McpCatalogEntry = {
  id: "github",
  label: "GitHub",
  summary: "",
  runner: "docker",
  available: true,
  command: "docker",
  args: [],
  env: {},
  secrets: [],
  containment: "",
  official: true,
  docs: "",
};

const SERVER: McpServer = { name: "s", command: "npx", args: [], env_keys: [], disabled: false };

describe("the official-vendor proof of a curated entry", () => {
  it("earns the badge with the flag AND a repository owned by one of the vendor's organisations", () => {
    expect(badgeOf({ proof: curatedProof({ ...ENTRY, repo: "GitHub/github-mcp-server", vendor_orgs: ["github"] }) })).toBe(
      "vendor",
    );
  });

  it("earns nothing from an older backend that sends no repository or organisations", () => {
    // The flag alone is the catalogue's claim, not a fact anyone can check.
    expect(badgeOf({ proof: curatedProof(ENTRY) })).toBeNull();
  });

  it("earns nothing when the catalogue does not call it official, whoever owns the repository", () => {
    const proof = curatedProof({ ...ENTRY, official: false, repo: "github/x", vendor_orgs: ["github"] });
    expect(badgeOf({ proof })).toBeNull();
  });
});

describe("who has to act on a configured server", () => {
  it("puts a held server and one waiting for its sign-in before the rest", () => {
    expect(serverStatus({ ...SERVER, manifest_held: { seen_at: 0, digest: "d", changes: [] } })).toBe("held");
    expect(serverStatus({ ...SERVER, oauth: { connected: false, identity: null } })).toBe("pending");
    expect(serverStatus({ ...SERVER, disabled: true })).toBe("inactive");
    expect(serverStatus({ ...SERVER, oauth: { connected: true, identity: null } })).toBe("active");
  });
});
