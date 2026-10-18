import { useId, useState } from "react";

import { Servers } from "@/components/Servers";
import { Tools } from "@/components/Tools";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

type Tab = "chimera" | "capabilities";

/**
 * Which agent you are looking at, and what it may reach.
 *
 * Tools is not read-only: each registered tool has a switch that writes CHIMERA_TOOL_DENYLIST, and
 * a tool that is off behind a setting can be switched on — but it is configuration you set once, not
 * something you visit every day. Honest framing for a settings tab, and a poor one for a top-level
 * icon.
 *
 * MCP servers and OpenAPI connectors were sub-tabs here too. They moved to Settings › Connectors:
 * those are things you install, and nothing is installed on this tab (study 04, §1.1).
 */
export function Connections() {
  const t = useT();
  const id = useId();
  const [tab, setTab] = useState<Tab>("chimera");

  const items = [
    // First, because it decides what every other screen is showing. The tool registry is about what
    // the agent can reach; this is about WHICH agent you are looking at.
    { value: "chimera" as const, label: t("settings.tab.server") },
    { value: "capabilities" as const, label: t("settings.tab.capabilities") },
  ];

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <Tabs items={items} value={tab} onChange={setTab} aria-label={t("settings.tab.connections")} />
      <div className="pt-4">
        <TabPanel tabsId={id} value={tab}>
          {tab === "chimera" && <Servers />}
          {tab === "capabilities" && <Tools embedded />}
        </TabPanel>
      </div>
    </div>
  );
}
