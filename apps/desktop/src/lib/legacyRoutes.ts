import type { View } from "@/components/IconRail";
import type { Route } from "@/lib/router";

export interface Redirect {
  readonly view: View;
  readonly params: Record<string, string>;
}

/**
 * The anchors the long Skills page offered, and where each section lives on the catalog screen. The
 * views do not exist yet (a later PR draws them); the address carries them now so a link written
 * today keeps meaning the same thing when they do.
 */
const SKILLS_ANCHORS: Readonly<Record<string, Record<string, string>>> = {
  "skills-catalog": { view: "discover" },
  "skills-library": { view: "discover", section: "chimera" },
  "skills-yours": { view: "mine" },
};

/**
 * An address that has moved, and where it goes now — or null when it has not (study 04, §1.3).
 *
 * Skills left Knowledge for Settings › Personalization. `#/knowledge?tab=skills` is in people's
 * bookmarks, in the CHANGELOG and in conversations the agent wrote, so it is rewritten rather than
 * dropped: an unknown tab already lands on Memory, which would look like the skills had vanished.
 *
 * The page's own section links were in-page anchors after the route, so they arrive glued to the
 * tab value — the hash router splits on the first `?` only, and `URLSearchParams` treats `#` as an
 * ordinary character: `tab=skills#skills-catalog` reads as tab `skills#skills-catalog`.
 */
export function legacyRedirect(route: Route): Redirect | null {
  const tab = route.params.get("tab");
  if (route.view === "knowledge" && tab !== null) {
    const [name, anchor] = tab.split("#", 2);
    if (name !== "skills") return null;
    const section = anchor ? SKILLS_ANCHORS[anchor] : undefined;
    return { view: "settings", params: { tab: "skills", ...(section ?? {}) } };
  }
  // MCP and OpenAPI were sub-tabs of Connections with no address of their own; someone typing one
  // gets the tab that holds them now, and the address says so.
  if (route.view === "settings" && (tab === "mcp" || tab === "openapi")) {
    const rest = Object.fromEntries([...route.params].filter(([key]) => key !== "tab"));
    return { view: "settings", params: { tab: "connectors", ...rest } };
  }
  return null;
}
