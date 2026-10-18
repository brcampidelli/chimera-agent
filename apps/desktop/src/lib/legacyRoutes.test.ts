import { describe, expect, it } from "vitest";

import { knowledgeTab } from "@/components/Knowledge";
import { settingsTab } from "@/components/Settings";
import { legacyRedirect } from "@/lib/legacyRoutes";
import { formatHash, parseHash } from "@/lib/router";

/** Where an address lands, as the address the app rewrites it to (or itself, when it stays). */
function lands(hash: string): string {
  const route = parseHash(hash, false);
  const moved = legacyRedirect(route);
  return moved ? formatHash(moved.view, moved.params) : hash;
}

/** One test per row of the address table in study 04, §1.3. */
describe("addresses that moved with Personalization", () => {
  it("sends Knowledge › Skills to Settings › Skills", () => {
    expect(lands("#/knowledge?tab=skills")).toBe("#/settings?tab=skills");
    // Still read by Knowledge until a release has carried the redirect — removal is a later PR.
    expect(knowledgeTab("skills")).toBe("skills");
  });

  it("keeps the old page's section anchors pointing at the same sections", () => {
    expect(lands("#/knowledge?tab=skills#skills-catalog")).toBe("#/settings?tab=skills&view=discover");
    expect(lands("#/knowledge?tab=skills#skills-library")).toBe(
      "#/settings?tab=skills&view=discover&section=chimera",
    );
    expect(lands("#/knowledge?tab=skills#skills-yours")).toBe("#/settings?tab=skills&view=mine");
    // An anchor that never named a section is dropped, not carried as a view nobody draws.
    expect(lands("#/knowledge?tab=skills#skills-active")).toBe("#/settings?tab=skills");
  });

  it("leaves Settings › Connections where it is", () => {
    expect(lands("#/settings?tab=connections")).toBe("#/settings?tab=connections");
    expect(settingsTab("connections")).toBe("connections");
  });

  it("sends the old MCP and OpenAPI names to Connectors, keeping anything else in the address", () => {
    expect(lands("#/settings?tab=mcp")).toBe("#/settings?tab=connectors");
    expect(lands("#/settings?tab=openapi")).toBe("#/settings?tab=connectors");
    expect(lands("#/settings?tab=mcp&q=github")).toBe("#/settings?tab=connectors&q=github");
  });

  it("leaves the new optional parameters alone", () => {
    const hash = "#/settings?tab=plugins&view=discover&q=pdf&cat=documents&item=plugin%3Apdf%40market";
    expect(lands(hash)).toBe(hash);
    expect(settingsTab(parseHash(hash, false).params.get("tab"))).toBe("plugins");
  });

  it("rewrites nothing else", () => {
    for (const hash of ["#/knowledge", "#/knowledge?tab=profile", "#/settings", "#/code?file=x.ts"]) {
      expect(lands(hash)).toBe(hash);
    }
  });
});
