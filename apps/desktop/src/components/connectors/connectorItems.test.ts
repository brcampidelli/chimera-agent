import { describe, expect, it } from "vitest";

import { badgeOf } from "@/components/catalog/model";
import { curatedProof, fromCuratedEntry, fromServer, serverStatus } from "@/components/connectors/connectorItems";
import type { McpCatalogEntry } from "@/lib/api";
import type { TFunc } from "@/lib/i18n";
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

describe("the network chip of a URL connector", () => {
  const t: TFunc = (key) => key;

  it("drops the chip for a curated URL entry addressed to loopback", () => {
    const item = fromCuratedEntry({ ...ENTRY, runner: "url", command: "", url: "http://127.0.0.1:8765/mcp" }, undefined, t);
    expect(item.capabilities.network).toBe(false);
  });

  it("keeps the chip for a curated URL entry on a remote host", () => {
    const item = fromCuratedEntry({ ...ENTRY, runner: "url", command: "", url: "https://mcp.example.test/mcp" }, undefined, t);
    expect(item.capabilities.network).toBe(true);
  });

  it("drops the chip for each loopback spelling of a configured URL server", () => {
    for (const host of ["127.0.0.1", "localhost", "::1"]) {
      const item = fromServer({ ...SERVER, command: "", url_host: host });
      expect(item.capabilities.network, host).toBe(false);
    }
  });

  it("keeps the chip for a configured URL server on a remote host, and never for a command", () => {
    expect(fromServer({ ...SERVER, command: "", url_host: "mcp.example.test" }).capabilities.network).toBe(true);
    expect(fromServer(SERVER).capabilities.network).toBe(false);
  });
});
