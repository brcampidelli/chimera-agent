import { useId, useState } from "react";

import { Mcp } from "@/components/Mcp";
import { OpenApiConnectors } from "@/components/OpenApiConnectors";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

type Tab = "mcp" | "openapi";

/**
 * What the owner plugs in: MCP servers and HTTP APIs described by an OpenAPI spec.
 *
 * Both used to be sub-tabs of Settings › Connections, beside the server picker and the tool
 * registry. Those two are set once and install nothing; these two are things you add, switch on and
 * remove, the same decisions a skill or a plugin asks for — so they sit with them under
 * Personalization (study 04, §1.1). The screens are embedded as they were; the shared catalog shell
 * that gives the three a common anatomy is a later PR.
 */
export function Connectors() {
  const t = useT();
  const id = useId();
  const [tab, setTab] = useState<Tab>("mcp");

  const items = [
    { value: "mcp" as const, label: t("nav.mcp") },
    { value: "openapi" as const, label: t("connectors.tab") },
  ];

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <Tabs items={items} value={tab} onChange={setTab} aria-label={t("settings.tab.connectors")} />
      <div className="pt-4">
        <TabPanel tabsId={id} value={tab}>
          {tab === "mcp" && <Mcp embedded />}
          {tab === "openapi" && <OpenApiConnectors />}
        </TabPanel>
      </div>
    </div>
  );
}
