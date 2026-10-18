import { useT } from "@/lib/i18n";
import type { CapabilityFacts } from "@/components/plugins/capabilities";

/**
 * What an item would DO on this machine, in one line: "instructions only" · "starts 2 MCP
 * server(s)" · "reaches the network" · "brings hooks: inert", or "known after installing".
 *
 * Promoted from `plugins/PluginParts` so skills and connectors draw the same line. It comes before
 * the author on every card, because it answers the first question a person has about a package.
 */
export function CapabilityLine({
  facts,
  id,
  limit,
}: {
  facts: CapabilityFacts;
  id?: string;
  /** What bounds it, said after what it runs (a connector's containment). Optional. */
  limit?: string | null;
}) {
  const t = useT();
  const parts: string[] = [];
  if (facts.unknown) parts.push(t("plugins.cap.unknown"));
  else if (facts.textOnly) parts.push(t("plugins.cap.text"));
  else {
    if (facts.servers > 0) parts.push(t("plugins.cap.servers", { n: facts.servers }));
    if (facts.network) parts.push(t("plugins.cap.network"));
    if (facts.inert > 0) parts.push(t("plugins.cap.inert"));
  }
  return (
    // Two lines at most with a limit: the whole sentence is in the detail, and a card that grows
    // with its longest containment would make the grid unreadable.
    <p id={id} className={limit ? "line-clamp-2 text-xs text-foreground" : "text-xs text-foreground"} data-testid="capability-line">
      {parts.join(" · ")}
      {limit ? <span className="text-muted-foreground">{` · ${limit}`}</span> : null}
    </p>
  );
}
