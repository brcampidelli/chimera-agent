import { useId, useState } from "react";

import { Mcp } from "@/components/Mcp";
import { Servers } from "@/components/Servers";
import { Tools } from "@/components/Tools";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

type Tab = "chimera" | "servers" | "capabilities";

/**
 * What the agent can reach.
 *
 * MCP servers and the tool registry answer the same question — what can this agent actually do
 * right now — and neither is a daily surface. MCP's own empty state says the CLI is the source of
 * truth and the app is a view over it; Tools is read-only introspection with no action available at
 * all. Honest framing for a settings tab, and a poor one for a top-level icon.
 */
export function Connections() {
  const t = useT();
  const id = useId();
  const [tab, setTab] = useState<Tab>("chimera");

  const items = [
    // First, because it decides what every other screen is showing. MCP and the tool registry are
    // about what the agent can reach; this is about WHICH agent you are looking at.
    { value: "chimera" as const, label: t("settings.tab.server") },
    { value: "servers" as const, label: t("nav.mcp") },
    { value: "capabilities" as const, label: t("settings.tab.capabilities") },
  ];

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <Tabs items={items} value={tab} onChange={setTab} aria-label={t("settings.tab.connections")} />
      <div className="pt-4">
        <TabPanel tabsId={id} value={tab}>
          {tab === "chimera" && <Servers />}
          {tab === "servers" && <Mcp embedded />}
          {tab === "capabilities" && <Tools embedded />}
        </TabPanel>
      </div>
    </div>
  );
}
