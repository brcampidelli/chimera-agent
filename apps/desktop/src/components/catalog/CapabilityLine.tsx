import { useT } from "@/lib/i18n";
import type { CapabilityFacts } from "@/components/plugins/capabilities";

/**
 * What an item would DO on this machine, in one line: "instructions only" · "starts 2 MCP
 * server(s)" · "reaches the network" · "brings hooks: inert", or "known after installing".
 *
 * Promoted from `plugins/PluginParts` so skills and connectors draw the same line. It comes before
 * the author on every card, because it answers the first question a person has about a package.
 */
export function CapabilityLine({ facts, id }: { facts: CapabilityFacts; id?: string }) {
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
    <p id={id} className="text-xs text-foreground" data-testid="capability-line">
      {parts.join(" · ")}
    </p>
  );
}
