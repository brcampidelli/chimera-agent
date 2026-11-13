export type SettingsTab =
  | "general"
  | "appearance"
  | "skills"
  | "connectors"
  | "plugins"
  | "connections"
  | "usage"
  | "security"
  | "decisions";

/** In the order the column shows them. */
export const SETTINGS_TABS: readonly SettingsTab[] = [
  "general",
  "appearance",
  "skills",
  "connectors",
  "plugins",
  "connections",
  "usage",
  "security",
  "decisions",
];

/**
 * Names a person may type for a tab that has another address. MCP servers and OpenAPI connectors
 * were sub-tabs of Connections kept in `useState`, so no link to them ever existed; both now live
 * under Connectors, and these are a courtesy for an address written by hand (study 04, §1.3).
 */
const SETTINGS_TAB_ALIASES: ReadonlyMap<string, SettingsTab> = new Map<string, SettingsTab>([
  ["mcp", "connectors"],
  ["openapi", "connectors"],
]);

/** A `?tab=` value from the URL, or General for anything that is not a tab — a stale link lands on
 *  the screen rather than on nothing. */
export function settingsTab(value: string | null | undefined): SettingsTab {
  const known = SETTINGS_TABS.find((tab) => tab === value);
  if (known) return known;
  return (value ? SETTINGS_TAB_ALIASES.get(value) : undefined) ?? "general";
}
